"""Error codes and exceptions shared by every service (docs/08 preamble)."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ERROR_STATUS",
    "CioError",
    "Conflict",
    "Forbidden",
    "KillSwitchActive",
    "LlmUnavailable",
    "NotFound",
    "PolicyBlocked",
    "TokenInvalid",
    "ValidationFailed",
]


class CioError(Exception):
    """Base for errors that map to a documented error code."""

    code = "INTERNAL"
    status = 500

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_payload(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class ValidationFailed(CioError):
    code, status = "VALIDATION", 422


class NotFound(CioError):
    code, status = "NOT_FOUND", 404


class Forbidden(CioError):
    code, status = "FORBIDDEN", 403


class Conflict(CioError):
    code, status = "CONFLICT", 409


class LlmUnavailable(CioError):
    """The gateway or its provider is down. Never fail open (CLAUDE.md §2.7)."""

    code, status = "LLM_UNAVAILABLE", 503


class PolicyBlocked(CioError):
    code, status = "POLICY_BLOCKED", 409


class TokenInvalid(CioError):
    code, status = "TOKEN_INVALID", 403


class KillSwitchActive(CioError):
    code, status = "KILL_SWITCH", 409


ERROR_STATUS: dict[str, int] = {
    cls.code: cls.status
    for cls in (
        ValidationFailed,
        NotFound,
        Forbidden,
        Conflict,
        LlmUnavailable,
        PolicyBlocked,
        TokenInvalid,
        KillSwitchActive,
    )
}
