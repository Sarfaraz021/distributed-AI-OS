from core.retry import NEVER_RETRY_STATUS, RETRY_STATUS, classify, full_jitter
from core.errors import FatalError, RetryableError


def test_full_jitter_stays_in_range():
    for attempt in range(6):
        delay = full_jitter(attempt, base_s=1.0, cap_s=8.0)
        assert 0 <= delay <= min(8.0, 1.0 * (2 ** attempt))


def test_classify_never_retries_client_errors():
    for status in NEVER_RETRY_STATUS:
        exc = RetryableError("nope", status_code=status)
        assert classify(exc) is FatalError


def test_classify_retries_transient_errors():
    for status in RETRY_STATUS:
        exc = FatalError("transient", status_code=status)
        assert classify(exc) is RetryableError
