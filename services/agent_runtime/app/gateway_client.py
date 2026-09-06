"""Talking to the LLM gateway (docs/06 §1, §6).

A thin client with a fake beside it, because every runtime behaviour worth
testing -- a schema refusal, an outage, a corrective turn -- has to be
exercised without a model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

__all__ = [
    "Answer",
    "FakeGatewayClient",
    "GatewayClient",
    "GatewayUnavailableError",
    "HttpGatewayClient",
    "SchemaRefusedError",
]


class GatewayUnavailableError(RuntimeError):
    """The gateway or its provider could not answer."""


class SchemaRefusedError(RuntimeError):
    """The gateway could not get a schema-valid answer out of the model."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors[:3]) or "schema refused")
        self.errors = errors


@dataclass(frozen=True, slots=True)
class Answer:
    """One completion, as the runtime needs it."""

    value: dict[str, Any] | None
    content: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""


class GatewayClient(Protocol):
    async def complete(
        self,
        *,
        route: str,
        messages: list[dict[str, Any]],
        json_schema: dict[str, Any] | None,
        max_tokens: int,
        temperature: float,
        run_id: str,
        budget_tokens: int,
    ) -> Answer: ...


class HttpGatewayClient:
    """The real client."""

    def __init__(
        self, base_url: str | None = None, *, timeout: float = 120.0, token: str | None = None
    ) -> None:
        self._base = (base_url or os.environ.get("LLM_GATEWAY_URL") or "http://llm_gateway:8020").rstrip("/")
        self._timeout = timeout
        self._token = token or os.environ.get("INTERNAL_TOKEN")

    async def complete(
        self,
        *,
        route: str,
        messages: list[dict[str, Any]],
        json_schema: dict[str, Any] | None,
        max_tokens: int,
        temperature: float,
        run_id: str,
        budget_tokens: int,
    ) -> Answer:
        body: dict[str, Any] = {
            "route": route,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "run_id": run_id,
        }
        if json_schema:
            body["json_schema"] = json_schema
        if budget_tokens:
            body["budget"] = {"tokens": budget_tokens}

        headers = {"authorization": f"Bearer {self._token}"} if self._token else {}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(f"{self._base}/llm/complete", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise GatewayUnavailableError(str(exc)) from exc

        if response.status_code == 503:
            raise GatewayUnavailableError(_message(response))
        if response.status_code == 422:
            # The model answered and its answer was wrong. That is a different
            # failure from the gateway being down, and the runtime treats it
            # differently: it degrades rather than retrying a dead provider.
            details = response.json().get("error", {}).get("details", {})
            raise SchemaRefusedError(list(details.get("errors") or [_message(response)]))
        response.raise_for_status()

        payload = response.json()
        return Answer(
            value=payload.get("json"),
            content=payload.get("content", ""),
            usage=payload.get("usage") or {},
            tool_calls=payload.get("tool_calls") or [],
            model=payload.get("model", ""),
        )


def _message(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["message"])
    except Exception:
        return response.text[:200]


@dataclass
class FakeGatewayClient:
    """A gateway that answers from a script."""

    replies: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    default: dict[str, Any] | None = None
    usage: dict[str, int] = field(default_factory=lambda: {"in": 50, "out": 30, "total": 80})

    async def complete(
        self,
        *,
        route: str,
        messages: list[dict[str, Any]],
        json_schema: dict[str, Any] | None,
        max_tokens: int,
        temperature: float,
        run_id: str,
        budget_tokens: int,
    ) -> Answer:
        self.calls.append(
            {
                "route": route,
                "messages": messages,
                "json_schema": json_schema,
                "max_tokens": max_tokens,
                "run_id": run_id,
            }
        )
        reply = self.replies.pop(0) if self.replies else self.default
        if isinstance(reply, Exception):
            raise reply
        return Answer(value=reply, usage=dict(self.usage), model="fake-1")
