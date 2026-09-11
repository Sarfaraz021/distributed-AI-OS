import core.llm as llm_mod
from core.config import get_settings
from core.llm import call_llm


def test_call_llm_returns_cached_result(memory_gateway):
    first = call_llm(
        [{"role": "user", "content": "hello"}],
        idem_key="k1",
        deadline_s=10,
    )
    second = call_llm(
        [{"role": "user", "content": "hello"}],
        idem_key="k1",
        deadline_s=10,
    )
    assert first == second
    assert first["content"].startswith("[fake")


def test_429_backoff_curve(memory_gateway, monkeypatch):
    delays: list[float] = []
    rolls = iter([0.0, 0.0, 0.9])

    monkeypatch.setenv("FAULT_INJECT_429", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(llm_mod.random, "random", lambda: next(rolls))
    monkeypatch.setattr(llm_mod.time, "sleep", lambda delay: delays.append(delay))

    result = call_llm(
        [{"role": "user", "content": "ping"}],
        idem_key="k-429",
        deadline_s=30,
        model="openai/gpt-4o-mini",
    )
    assert result["content"].startswith("[fake")
    assert len(delays) == 2
    assert all(delay >= 0 for delay in delays)
