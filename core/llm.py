from __future__ import annotations

import logging
import random
import time
from typing import Any, Iterable

import litellm
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from core.circuit_breaker import breaker_for
from core.config import get_settings
from core.errors import DeadlineExceeded, FatalError, RetryableError, RetryBudgetExceeded
from core.idempotency import execute_idempotent
from core.retry import RetryBudget, classify, full_jitter

logger = logging.getLogger(__name__)

litellm.num_retries = 0
litellm.drop_params = True

_budget = RetryBudget()


def retry_budget() -> RetryBudget:
    return _budget


def provider_from_model(model: str) -> str:
    if "/" in model:
        return model.split("/", 1)[0]
    return "unknown"


def _as_openai_messages(messages: Iterable[Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for message in messages:
        if isinstance(message, dict):
            out.append({"role": message["role"], "content": str(message.get("content", ""))})
            continue
        if isinstance(message, SystemMessage):
            role = "system"
        elif isinstance(message, HumanMessage):
            role = "user"
        elif isinstance(message, AIMessage):
            role = "assistant"
        elif isinstance(message, BaseMessage):
            role = message.type
        else:
            raise TypeError(f"unsupported message type: {type(message)!r}")
        out.append({"role": role, "content": str(message.content)})
    return out


def _inject_429() -> None:
    settings = get_settings()
    if settings.fault_inject_429 and random.random() < 0.5:
        raise RetryableError("fault-injected 429", status_code=429)


def _fake_complete(model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    last = messages[-1]["content"] if messages else ""
    text = f"[fake {model}] {last[:240]}"
    return {"content": text, "model": model, "provider": provider_from_model(model), "usage": {}}


def _live_complete(model: str, messages: list[dict[str, str]], timeout_s: float) -> dict[str, Any]:
    response = litellm.completion(
        model=model,
        messages=messages,
        timeout=timeout_s,
        num_retries=0,
    )
    choice = response.choices[0].message
    usage = getattr(response, "usage", None)
    return {
        "content": choice.content or "",
        "model": getattr(response, "model", model),
        "provider": provider_from_model(model),
        "usage": dict(usage) if usage else {},
    }


def call_llm(messages, *, idem_key: str, deadline_s: float, model: str | None = None) -> dict[str, Any]:
    """Idempotent LLM call with deadline, classify-then-retry, full jitter, budget, breaker."""

    settings = get_settings()
    model = model or settings.llm_model
    provider = provider_from_model(model)
    payload = _as_openai_messages(messages)
    breaker = breaker_for(
        provider,
        fail_threshold=settings.circuit_fail_threshold,
        cooldown_s=settings.circuit_cooldown_s,
        half_open_max=settings.circuit_half_open_max,
    )
    deadline_at = time.monotonic() + deadline_s

    def _once() -> dict[str, Any]:
        breaker.allow()
        _budget.record_call()
        last_error: BaseException | None = None

        for attempt in range(settings.max_attempts):
            remaining = deadline_at - time.monotonic()
            if remaining <= 0:
                raise DeadlineExceeded("llm deadline exceeded")

            if attempt > 0:
                if not _budget.allow_retry():
                    logger.warning("retry_budget_exhausted %s", _budget.snapshot())
                    raise RetryBudgetExceeded("retry budget exhausted")
                delay = full_jitter(
                    attempt - 1,
                    base_s=settings.retry_base_s,
                    cap_s=settings.retry_cap_s,
                )
                delay = min(delay, remaining)
                logger.info(
                    "llm_backoff provider=%s attempt=%s/%s delay_s=%.3f remaining_s=%.3f budget=%s",
                    provider,
                    attempt + 1,
                    settings.max_attempts,
                    delay,
                    remaining,
                    _budget.snapshot(),
                )
                time.sleep(delay)
                remaining = deadline_at - time.monotonic()
                if remaining <= 0:
                    raise DeadlineExceeded("llm deadline exceeded during backoff")
                _budget.record_retry()

            try:
                _inject_429()
                if settings.llm_backend == "live":
                    result = _live_complete(model, payload, timeout_s=remaining)
                else:
                    result = _fake_complete(model, payload)
                breaker.on_success()
                logger.info(
                    "llm_ok provider=%s attempt=%s cached=false",
                    provider,
                    attempt + 1,
                )
                return result
            except Exception as exc:
                kind = classify(exc)
                last_error = kind(str(exc), status_code=getattr(exc, "status_code", None))
                logger.warning(
                    "llm_error provider=%s attempt=%s/%s class=%s status=%s err=%s",
                    provider,
                    attempt + 1,
                    settings.max_attempts,
                    kind.__name__,
                    getattr(exc, "status_code", None),
                    exc,
                )
                if kind is FatalError:
                    breaker.on_failure()
                    raise last_error from exc
                if attempt == settings.max_attempts - 1:
                    breaker.on_failure()
                    raise last_error from exc

        assert last_error is not None
        raise last_error

    result, cached = execute_idempotent(idem_key, _once)
    if cached:
        logger.info("llm_idempotency_hit key=%s provider=%s", idem_key[:12], provider)
    return result
