from __future__ import annotations

import random
import threading

from core.errors import FatalError, RetryableError

RETRY_STATUS = {429, 500, 502, 503, 504}
NEVER_RETRY_STATUS = {400, 401, 403, 422}


def full_jitter(attempt: int, *, base_s: float, cap_s: float) -> float:
    """AWS full jitter: sleep = random(0, min(cap, base * 2 ** attempt))."""
    ceiling = min(cap_s, base_s * (2 ** attempt))
    return random.uniform(0, ceiling)


def classify(exc: BaseException) -> type[RetryableError] | type[FatalError]:
    status = getattr(exc, "status_code", None)
    if status in NEVER_RETRY_STATUS:
        return FatalError
    if status in RETRY_STATUS:
        return RetryableError

    name = type(exc).__name__.lower()
    retry_names = (
        "ratelimit",
        "timeout",
        "serviceunavailable",
        "internalserver",
        "apiconnection",
        "apierror",
        "retryable",
    )
    fatal_names = (
        "auth",
        "permission",
        "badrequest",
        "invalidrequest",
        "notfound",
        "contentpolicy",
        "unprocessable",
    )
    if any(token in name for token in fatal_names):
        return FatalError
    if any(token in name for token in retry_names):
        return RetryableError
    if isinstance(exc, RetryableError):
        return RetryableError
    if isinstance(exc, FatalError):
        return FatalError
    return FatalError


class RetryBudget:
    """Caps retries as a fraction of total attempts (Marc Brooker / AWS)."""

    def __init__(self, max_ratio: float = 0.2):
        self.max_ratio = max_ratio
        self._lock = threading.Lock()
        self.calls = 0
        self.retries = 0

    def record_call(self) -> None:
        with self._lock:
            self.calls += 1

    def allow_retry(self) -> bool:
        with self._lock:
            if self.calls < 10:
                return True
            return (self.retries / self.calls) < self.max_ratio

    def record_retry(self) -> None:
        with self._lock:
            self.retries += 1

    def snapshot(self) -> dict[str, float | int]:
        with self._lock:
            ratio = (self.retries / self.calls) if self.calls else 0.0
            return {"calls": self.calls, "retries": self.retries, "ratio": round(ratio, 3)}
