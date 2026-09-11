import pytest

from core.circuit_breaker import CircuitBreaker, CircuitState
from core.errors import CircuitOpenError


def test_opens_after_threshold_and_fails_fast():
    breaker = CircuitBreaker("openai", fail_threshold=2, cooldown_s=60)
    breaker.allow()
    breaker.on_failure()
    breaker.allow()
    breaker.on_failure()
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.allow()


def test_half_open_success_closes():
    breaker = CircuitBreaker("anthropic", fail_threshold=1, cooldown_s=0)
    breaker.on_failure()
    breaker.allow()
    assert breaker.state is CircuitState.HALF_OPEN
    breaker.on_success()
    assert breaker.state is CircuitState.CLOSED
