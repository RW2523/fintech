"""Service-to-service HTTP with correlation and internal auth (docs/13 §1)."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

import httpx

from cio_common.errors import (
    CioError,
    Conflict,
    Forbidden,
    KillSwitchActive,
    LlmUnavailable,
    NotFound,
    PolicyBlocked,
    TokenInvalid,
    ValidationFailed,
)
from cio_common.otel import current_trace_id
from cio_common.settings import get_settings

__all__ = ["ServiceClient", "raise_for_error", "service_client"]

_BY_CODE: dict[str, type[CioError]] = {
    "VALIDATION": ValidationFailed,
    "NOT_FOUND": NotFound,
    "FORBIDDEN": Forbidden,
    "CONFLICT": Conflict,
    "LLM_UNAVAILABLE": LlmUnavailable,
    "POLICY_BLOCKED": PolicyBlocked,
    "TOKEN_INVALID": TokenInvalid,
    "KILL_SWITCH": KillSwitchActive,
}


def raise_for_error(response: httpx.Response) -> None:
    """Turn a documented error envelope back into its exception type."""
    if response.is_success:
        return
    code: str = "INTERNAL"
    message: str = response.text[:300]
    details: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        body = response.json().get("error", {})
        code = body.get("code", code)
        message = body.get("message", message)
        details = body.get("details", {}) or {}
    raise _BY_CODE.get(code, CioError)(message, **details)


class ServiceClient:
    """A thin httpx wrapper that propagates the trace id and internal key."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        settings = get_settings()
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={
                "X-Internal-Key": settings.internal_key,
                "User-Agent": f"cio-{settings.service_name}/0.1.0",
            },
        )

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        trace_id = current_trace_id()
        if trace_id:
            headers.setdefault("X-Trace-Id", trace_id)
        response = await self._client.request(method, url, headers=headers, **kwargs)
        raise_for_error(response)
        return response

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        return (await self.request("GET", url, **kwargs)).json()

    async def post_json(self, url: str, json: Any = None, **kwargs: Any) -> Any:
        return (await self.request("POST", url, json=json, **kwargs)).json()

    async def aclose(self) -> None:
        await self._client.aclose()


@contextlib.asynccontextmanager
async def service_client(base_url: str, **kwargs: Any) -> AsyncIterator[ServiceClient]:
    client = ServiceClient(base_url, **kwargs)
    try:
        yield client
    finally:
        await client.aclose()
