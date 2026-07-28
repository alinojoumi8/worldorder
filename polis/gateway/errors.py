"""Uniform external protocol errors with fixed, non-reflective messages."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Final


class ErrorCode(StrEnum):
    NOT_ADMITTED = "NOT_ADMITTED"
    REVOKED = "REVOKED"
    SUSPENDED = "SUSPENDED"
    SESSION_INVALID = "SESSION_INVALID"
    BAD_SIGNATURE = "BAD_SIGNATURE"
    NONCE_REUSED = "NONCE_REUSED"
    DUPLICATE_ACTION_ID = "DUPLICATE_ACTION_ID"
    TICK_MISMATCH = "TICK_MISMATCH"
    LATE = "LATE"
    NO_SLOTS = "NO_SLOTS"
    UNKNOWN_ACTION_TYPE = "UNKNOWN_ACTION_TYPE"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    RATE_LIMITED = "RATE_LIMITED"
    QUEUE_FULL = "QUEUE_FULL"
    NOT_VISIBLE = "NOT_VISIBLE"
    GATEWAY_DEGRADED = "GATEWAY_DEGRADED"


HTTP_STATUS: Final[Mapping[ErrorCode, int]] = {
    ErrorCode.NOT_ADMITTED: 403,
    ErrorCode.REVOKED: 403,
    ErrorCode.SUSPENDED: 403,
    ErrorCode.SESSION_INVALID: 401,
    ErrorCode.BAD_SIGNATURE: 401,
    ErrorCode.NONCE_REUSED: 409,
    ErrorCode.DUPLICATE_ACTION_ID: 409,
    ErrorCode.TICK_MISMATCH: 409,
    ErrorCode.LATE: 409,
    ErrorCode.NO_SLOTS: 409,
    ErrorCode.UNKNOWN_ACTION_TYPE: 422,
    ErrorCode.SCHEMA_INVALID: 422,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.QUEUE_FULL: 503,
    ErrorCode.NOT_VISIBLE: 404,
    ErrorCode.GATEWAY_DEGRADED: 503,
}

MESSAGES: Final[Mapping[ErrorCode, str]] = {
    ErrorCode.NOT_ADMITTED: "Your registration is still pending admission.",
    ErrorCode.REVOKED: "This key has been revoked.",
    ErrorCode.SUSPENDED: "This identity is temporarily suspended.",
    ErrorCode.SESSION_INVALID: "Your session is invalid or expired.",
    ErrorCode.BAD_SIGNATURE: "The request signature is invalid.",
    ErrorCode.NONCE_REUSED: "This nonce has already been used.",
    ErrorCode.DUPLICATE_ACTION_ID: "This action identifier has already been used.",
    ErrorCode.TICK_MISMATCH: "This action does not name the open tick.",
    ErrorCode.LATE: "The action arrived after the tick was sealed.",
    ErrorCode.NO_SLOTS: "You have already acted this tick.",
    ErrorCode.UNKNOWN_ACTION_TYPE: "The action type is not available.",
    ErrorCode.SCHEMA_INVALID: "The request does not match the required schema.",
    ErrorCode.PAYLOAD_TOO_LARGE: "The request is too large.",
    ErrorCode.RATE_LIMITED: "The request limit has been reached.",
    ErrorCode.QUEUE_FULL: "The action queue is full for this tick.",
    ErrorCode.NOT_VISIBLE: "The requested record is not visible.",
    ErrorCode.GATEWAY_DEGRADED: "The gateway is temporarily degraded.",
}

RETRYABLE: Final[Mapping[ErrorCode, bool]] = {
    ErrorCode.NOT_ADMITTED: True,
    ErrorCode.REVOKED: False,
    ErrorCode.SUSPENDED: True,
    ErrorCode.SESSION_INVALID: True,
    ErrorCode.BAD_SIGNATURE: False,
    ErrorCode.NONCE_REUSED: True,
    ErrorCode.DUPLICATE_ACTION_ID: False,
    ErrorCode.TICK_MISMATCH: True,
    ErrorCode.LATE: False,
    ErrorCode.NO_SLOTS: True,
    ErrorCode.UNKNOWN_ACTION_TYPE: False,
    ErrorCode.SCHEMA_INVALID: True,
    ErrorCode.PAYLOAD_TOO_LARGE: False,
    ErrorCode.RATE_LIMITED: True,
    ErrorCode.QUEUE_FULL: False,
    ErrorCode.NOT_VISIBLE: False,
    ErrorCode.GATEWAY_DEGRADED: True,
}


class ProtocolError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        *,
        retry_after_ms: int | None = None,
        strikes: int = 0,
    ) -> None:
        super().__init__(MESSAGES[code])
        self.code = code
        self.retry_after_ms = retry_after_ms
        self.strikes = strikes


def envelope(error: ProtocolError, *, tick: int) -> Mapping[str, object]:
    return {
        "error": {
            "code": error.code.value,
            "message": MESSAGES[error.code],
            "retryable": RETRYABLE[error.code],
            "tick": tick,
            "strikes": error.strikes,
            "retry_after_ms": error.retry_after_ms,
        }
    }
