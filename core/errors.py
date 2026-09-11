class GatewayError(Exception):
    """Base error for the LLM/tool gateway."""


class RetryableError(GatewayError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class FatalError(GatewayError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class CircuitOpenError(FatalError):
    pass


class DeadlineExceeded(FatalError):
    pass


class RetryBudgetExceeded(FatalError):
    pass


class IdempotencyInProgressError(RetryableError):
    pass
