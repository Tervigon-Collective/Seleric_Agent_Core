"""Common ad-platform error base so the envelope layer handles Meta and Google
uniformly. Each platform's client raises a subclass that knows how to map its
native error to a blueprint §15 error code via ``error_code()``.
"""

from __future__ import annotations


class AdsApiError(RuntimeError):
    """A structured ad-platform error (or transport failure)."""

    platform = "ads"

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        code: object | None = None,
        subcode: object | None = None,
        request_id: str | None = None,
        error_type: str | None = None,
        user_message: str | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.code = code
        self.subcode = subcode
        self.request_id = request_id
        self.error_type = error_type
        self.user_message = user_message
        self.retryable = retryable

    def error_code(self) -> str:
        """Blueprint §15 code. Overridden per platform."""
        return "INTERNAL_ERROR"

    def details(self) -> dict:
        return {
            "subcode": self.subcode,
            "type": self.error_type,
            "user_message": self.user_message,
            "http_status": self.http_status,
        }
