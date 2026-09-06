"""One HTTP client for every tool (docs/06 §4).

Tools are the only way an agent reaches data, and each one is a read against
the service that owns that data. They share this client so the timeout,
failure translation and base-URL resolution are decided once.

A tool that cannot reach its service raises rather than returning an empty
result. An empty result reads as "there is nothing", which is a different
claim from "I could not look".
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

__all__ = ["FakeServices", "ServiceCall", "Services", "ToolBackendError", "services", "set_services"]

#: Where each backing service lives on the compose network (docs/01 §4).
DEFAULT_PORTS = {
    "application": 8001,
    "document": 8002,
    "member_intelligence": 8003,
    "policy": 8004,
    "feature": 8005,
    "risk": 8006,
    "fraud": 8007,
    "lmi": 8008,
    "committee": 8009,
    "core_stub": 8010,
    "agent_runtime": 8011,
    "decision": 8012,
    "execution": 8013,
    "notification": 8014,
    "audit": 8015,
    "governance": 8016,
}

DEFAULT_TIMEOUT = 20.0


class ToolBackendError(RuntimeError):
    """The service behind a tool could not answer."""


class Services(Protocol):
    async def get(self, service: str, path: str, **params: Any) -> Any: ...

    async def post(self, service: str, path: str, body: dict[str, Any] | None = None) -> Any: ...


@dataclass(frozen=True, slots=True)
class ServiceCall:
    """One call a fake recorded, so a test can see what a tool asked for."""

    service: str
    method: str
    path: str
    params: dict[str, Any] = field(default_factory=dict)
    body: dict[str, Any] | None = None


class HttpServices:
    """The real client."""

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    def base_url(self, service: str) -> str:
        if override := os.environ.get(f"{service.upper()}_URL"):
            return override.rstrip("/")
        host = os.environ.get(f"UPSTREAM_HOST_{service.upper()}", service)
        return f"http://{host}:{DEFAULT_PORTS.get(service, 8000)}"

    async def _request(
        self,
        service: str,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url(service)}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method, url, params={k: v for k, v in (params or {}).items() if v is not None}, json=body
                )
        except httpx.HTTPError as exc:
            raise ToolBackendError(f"{service} unreachable: {exc}") from exc
        if response.status_code == 404:
            raise ToolBackendError(f"{service} has no {path}")
        if response.status_code >= 400:
            raise ToolBackendError(f"{service} refused {path}: {response.status_code} {response.text[:200]}")
        return response.json()

    async def get(self, service: str, path: str, **params: Any) -> Any:
        return await self._request(service, "GET", path, params=params)

    async def post(self, service: str, path: str, body: dict[str, Any] | None = None) -> Any:
        return await self._request(service, "POST", path, body=body)


@dataclass
class FakeServices:
    """Canned responses, keyed by `service path`.

    Tools are thin by design, so what is worth testing is what they ask for and
    how they shape what comes back. A fake makes both visible.
    """

    responses: dict[str, Any] = field(default_factory=dict)
    calls: list[ServiceCall] = field(default_factory=list)

    def add(self, service: str, path: str, payload: Any) -> None:
        self.responses[f"{service} {path}"] = payload

    def _lookup(self, service: str, path: str) -> Any:
        key = f"{service} {path}"
        if key in self.responses:
            payload = self.responses[key]
            if isinstance(payload, Exception):
                raise payload
            return payload
        raise ToolBackendError(f"no fake response for {key}")

    async def get(self, service: str, path: str, **params: Any) -> Any:
        self.calls.append(ServiceCall(service, "GET", path, dict(params)))
        return self._lookup(service, path)

    async def post(self, service: str, path: str, body: dict[str, Any] | None = None) -> Any:
        self.calls.append(ServiceCall(service, "POST", path, {}, body))
        return self._lookup(service, path)


_services: Services = HttpServices()


def services() -> Services:
    return _services


def set_services(replacement: Services | None) -> None:
    global _services
    _services = replacement or HttpServices()
