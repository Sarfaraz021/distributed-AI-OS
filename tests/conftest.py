from __future__ import annotations

import pytest

from core.circuit_breaker import reset_breakers
from core.config import get_settings
from core.idempotency import MemoryIdempotencyStore, set_store


@pytest.fixture
def memory_gateway(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "fake")
    monkeypatch.setenv("FAULT_INJECT_429", "false")
    monkeypatch.setenv("CRASH_AFTER_EMAIL", "false")
    get_settings.cache_clear()
    store = MemoryIdempotencyStore()
    set_store(store)
    reset_breakers()
    yield store
    get_settings.cache_clear()
    reset_breakers()
