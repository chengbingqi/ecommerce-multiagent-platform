ERROR_CODES = (
    "INVALID_ARGUMENT",
    "UNAUTHENTICATED",
    "FORBIDDEN",
    "NOT_FOUND",
    "APPROVAL_REQUIRED",
    "IDEMPOTENCY_CONFLICT",
    "FEATURE_NOT_ENABLED",
    "TIMEOUT",
    "TRANSIENT_ERROR",
    "INTERNAL_ERROR",
)


class ToolFailure(Exception):
    def __init__(self, error_code: str, message: str, *, retryable: bool = False) -> None:
        if error_code not in ERROR_CODES:
            raise ValueError(f"unknown tool error code: {error_code}")
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.retryable = retryable
