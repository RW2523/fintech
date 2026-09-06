"""What execution-service talks to (docs/08 §7).

It owns no decision and no ledger. It asks the decision service whether a token
is good and consumes it, asks the policy service whether the platform is
stopped, and writes to the core. Each has a fake beside it so a saga can be
driven through every branch, including the ones that only happen when a core
write fails halfway.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

__all__ = ["Clients", "CoreRefusedError", "FakeClients", "HttpClients", "TokenRefusedError", "UpstreamError"]


class UpstreamError(RuntimeError):
    """A service this one depends on could not answer."""


class TokenRefusedError(RuntimeError):
    """The decision service would not accept the token.

    Distinct from UpstreamError: a refused token is an answer, and the right
    response is to refuse the execution, not to retry it.
    """

    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.detail = detail or {}


class CoreRefusedError(RuntimeError):
    """The core rejected a write. Retryable; the case stays pending."""


class Clients(Protocol):
    async def validate_token(self, token_id: str, **scope: Any) -> dict[str, Any]: ...

    async def redeem_token(self, token_id: str, **scope: Any) -> dict[str, Any]: ...

    async def kill_switch_active(self, product_code: str) -> bool: ...

    async def core_activate(self, body: dict[str, Any], token: str) -> dict[str, Any]: ...

    async def core_status(self, body: dict[str, Any], token: str) -> dict[str, Any]: ...

    async def notify(self, body: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class HttpClients:
    decision_url: str = field(default_factory=lambda: os.environ.get("DECISION_URL", "http://decision:8012"))
    policy_url: str = field(default_factory=lambda: os.environ.get("POLICY_URL", "http://policy:8004"))
    core_url: str = field(default_factory=lambda: os.environ.get("CORE_URL", "http://core_stub:8010"))
    notification_url: str = field(
        default_factory=lambda: os.environ.get("NOTIFICATION_URL", "http://notification:8014")
    )
    timeout: float = 30.0

    async def _call(
        self, method: str, url: str, *, json: Any = None, params: Any = None, headers: Any = None
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(method, url, json=json, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"{url}: {exc}") from exc
        if response.status_code >= 400:
            body = _error_of(response)
            raise _classify(url, response.status_code, body)
        return dict(response.json())

    async def validate_token(self, token_id: str, **scope: Any) -> dict[str, Any]:
        return await self._call(
            "GET", f"{self.decision_url.rstrip('/')}/tokens/{token_id}/validate", params=scope
        )

    async def redeem_token(self, token_id: str, **scope: Any) -> dict[str, Any]:
        return await self._call(
            "POST", f"{self.decision_url.rstrip('/')}/tokens/{token_id}/redeem", params=scope
        )

    async def kill_switch_active(self, product_code: str) -> bool:
        dial = await self._call("GET", f"{self.policy_url.rstrip('/')}/autonomy/{product_code}")
        return bool((dial.get("kill_switch") or {}).get("enabled"))

    async def core_activate(self, body: dict[str, Any], token: str) -> dict[str, Any]:
        return await self._call(
            "POST",
            f"{self.core_url.rstrip('/')}/core/write/activate",
            json=body,
            headers={"X-Approval-Token": token},
        )

    async def core_status(self, body: dict[str, Any], token: str) -> dict[str, Any]:
        return await self._call(
            "POST",
            f"{self.core_url.rstrip('/')}/core/write/status",
            json=body,
            headers={"X-Approval-Token": token},
        )

    async def notify(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", f"{self.notification_url.rstrip('/')}/notifications", json=body)


def _error_of(response: httpx.Response) -> dict[str, Any]:
    try:
        return dict(response.json().get("error") or {})
    except Exception:
        return {"message": response.text[:300]}


def _classify(url: str, status: int, body: dict[str, Any]) -> Exception:
    """Which failures are answers and which are outages.

    A refused token and a rejected write are answers: retrying them changes
    nothing and hides the reason. A 5xx or a timeout is an outage, and the
    action stays pending so it can be retried.
    """
    message = str(body.get("message") or f"{status}")
    if body.get("code") in ("TOKEN_INVALID", "FORBIDDEN") or status == 403:
        return TokenRefusedError(message, body.get("details"))
    if status in (400, 404, 409, 422) and "/core/" in url:
        return CoreRefusedError(f"{url}: {message}")
    if status < 500 and "/core/" not in url:
        return TokenRefusedError(message, body.get("details"))
    return UpstreamError(f"{url}: {status} {message}")


@dataclass
class FakeClients:
    """Scripted upstreams. Any value may be an exception, which is raised."""

    tokens: dict[str, Any] = field(default_factory=dict)
    redeemed: dict[str, Any] = field(default_factory=dict)
    stopped: set[str] = field(default_factory=set)
    activate: Any = None
    #: The answer to every status write. Overridden per requested status by
    #: `status_by_value`, so a test can fail the approval and still let the
    #: rollback succeed, which is the case the saga exists for.
    status: Any = None
    status_by_value: dict[str, Any] = field(default_factory=dict)
    notification: Any = None
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def _answer(self, value: Any, default: Any) -> Any:
        if isinstance(value, Exception):
            raise value
        return default if value is None else value

    async def validate_token(self, token_id: str, **scope: Any) -> dict[str, Any]:
        self.calls.append(("validate_token", {"token_id": token_id, **scope}))
        return dict(self._answer(self.tokens.get(token_id), {"token_id": token_id, "scope": {}}))

    async def redeem_token(self, token_id: str, **scope: Any) -> dict[str, Any]:
        self.calls.append(("redeem_token", {"token_id": token_id, **scope}))
        return dict(self._answer(self.redeemed.get(token_id), {"redeemed": True, "token_id": token_id}))

    async def kill_switch_active(self, product_code: str) -> bool:
        self.calls.append(("kill_switch_active", product_code))
        return product_code in self.stopped

    async def core_activate(self, body: dict[str, Any], token: str) -> dict[str, Any]:
        self.calls.append(("core_activate", body))
        return dict(
            self._answer(
                self.activate,
                {"action": "activate", "account_id": "A-000001", "status": "ACTIVE", "replayed": False},
            )
        )

    async def core_status(self, body: dict[str, Any], token: str) -> dict[str, Any]:
        self.calls.append(("core_status", body))
        wanted = str(body.get("status"))
        scripted = self.status_by_value.get(wanted, self.status)
        return dict(
            self._answer(
                scripted,
                {"action": "status", "account_id": body.get("account_id"), "status": wanted},
            )
        )

    async def notify(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("notify", body))
        return dict(self._answer(self.notification, {"notification_id": "msg_fake", "state": "QUEUED"}))
