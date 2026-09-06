"""What the orchestrator talks to (docs/06 §8).

The committee service runs the state machine and owns none of the work. It
asks the agent runtime for opinions, the policy service for the synthesis, and
the decision service to record the result. Each has a fake beside it so a run
can be exercised without any of them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

__all__ = ["Clients", "FakeClients", "HttpClients", "UpstreamError"]


class UpstreamError(RuntimeError):
    """A service the run depends on could not answer."""


class Clients(Protocol):
    async def invoke_agent(self, body: dict[str, Any]) -> dict[str, Any]: ...

    async def synthesize(self, body: dict[str, Any]) -> dict[str, Any]: ...

    async def narrate(self, body: dict[str, Any]) -> dict[str, Any]: ...

    async def record(self, body: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class HttpClients:
    """The real upstreams."""

    agent_runtime_url: str = field(
        default_factory=lambda: os.environ.get("AGENT_RUNTIME_URL", "http://agent_runtime:8011")
    )
    policy_url: str = field(default_factory=lambda: os.environ.get("POLICY_URL", "http://policy:8004"))
    decision_url: str = field(default_factory=lambda: os.environ.get("DECISION_URL", "http://decision:8012"))
    llm_url: str = field(default_factory=lambda: os.environ.get("LLM_GATEWAY_URL", "http://llm_gateway:8020"))
    timeout: float = 300.0

    async def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=body)
                response.raise_for_status()
                return dict(response.json())
        except httpx.HTTPError as exc:
            raise UpstreamError(f"{url}: {exc}") from exc

    async def invoke_agent(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post(f"{self.agent_runtime_url.rstrip('/')}/agents/invoke", body)

    async def synthesize(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post(f"{self.policy_url.rstrip('/')}/policy/synthesize", body)

    async def narrate(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post(f"{self.llm_url.rstrip('/')}/llm/complete", body)

    async def record(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post(f"{self.decision_url.rstrip('/')}/recommendations", body)


@dataclass
class FakeClients:
    """Scripted upstreams, so a run can be driven through every branch."""

    opinions: dict[str, Any] = field(default_factory=dict)
    synthesis: dict[str, Any] | None = None
    narration: dict[str, Any] | None = None
    recorded: list[dict[str, Any]] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def invoke_agent(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("invoke", body))
        reply = self.opinions.get(body["agent_id"])
        if isinstance(reply, Exception):
            raise reply
        if reply is None:
            raise UpstreamError(f"no scripted opinion for {body['agent_id']}")
        return reply

    async def synthesize(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("synthesize", body))
        if isinstance(self.synthesis, Exception):
            raise self.synthesis
        return self.synthesis or {}

    async def narrate(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("narrate", body))
        if isinstance(self.narration, Exception):
            raise self.narration
        return self.narration or {"json": {"member": "", "officer": "", "auditor": ""}}

    async def record(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("record", body))
        self.recorded.append(body)
        return {
            "decision_record_id": body.get("decision_record_id", "dr_fake"),
            "ledger_entry_id": "ent_fake",
        }
