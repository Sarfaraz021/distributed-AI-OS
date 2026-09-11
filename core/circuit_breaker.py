from __future__ import annotations

import threading
import time
from enum import Enum

from core.errors import CircuitOpenError


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        fail_threshold: int = 3,
        cooldown_s: float = 20.0,
        half_open_max: int = 1,
    ):
        self.name = name
        self.fail_threshold = fail_threshold
        self.cooldown_s = cooldown_s
        self.half_open_max = half_open_max
        self._lock = threading.Lock()
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.opened_at = 0.0
        self.half_open_probes = 0

    def allow(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self.state is CircuitState.OPEN:
                if now - self.opened_at >= self.cooldown_s:
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_probes = 0
                else:
                    raise CircuitOpenError(
                        f"circuit open for provider={self.name}",
                        status_code=503,
                    )
            if self.state is CircuitState.HALF_OPEN:
                if self.half_open_probes >= self.half_open_max:
                    raise CircuitOpenError(
                        f"circuit half-open, probe in flight for provider={self.name}",
                        status_code=503,
                    )
                self.half_open_probes += 1

    def on_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.half_open_probes = 0
            self.state = CircuitState.CLOSED

    def on_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.state is CircuitState.HALF_OPEN or self.failures >= self.fail_threshold:
                self.state = CircuitState.OPEN
                self.opened_at = time.monotonic()
                self.half_open_probes = 0


_breakers: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()


def breaker_for(provider: str, **kwargs) -> CircuitBreaker:
    with _registry_lock:
        if provider not in _breakers:
            _breakers[provider] = CircuitBreaker(provider, **kwargs)
        return _breakers[provider]


def reset_breakers() -> None:
    with _registry_lock:
        _breakers.clear()
