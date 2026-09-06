"""T-050 — the workflow's own tool calls (docs/06 §8).

During a Tier 2 repair the orchestrator calls the tool the Challenger asked
for. This is that endpoint: who may use it, what it may reach, and what it
records.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

RUN = "run_01ARZ3NDEKTSV4RRFFQ69G5FAX"
CASE = "case_01ARZ3NDEKTSV4RRFFQ69G5FAW"


async def call(client: AsyncClient, **body: Any) -> Any:
    payload = {"tool": "policy.lookup", "args": {"query": "affordability"}, "committee_run_id": RUN}
    payload.update(body)
    return await client.post("/tools/call", json=payload)


async def test_the_registry_is_listed(client: AsyncClient) -> None:
    body = (await client.get("/tools")).json()
    assert "policy.lookup" in body["tools"]
    assert body["count"] == len(body["tools"])


async def test_a_read_tool_answers_and_is_recorded(client: AsyncClient) -> None:
    response = await call(client)
    assert response.status_code == 200
    body = response.json()
    assert body["tool"] == "policy.lookup"
    assert body["call_id"], "the call was not recorded"
    assert body["result"] is not None


async def test_only_the_workflow_may_call(client: AsyncClient) -> None:
    """An agent calling a tool outside its own invocation would be reaching
    past the grants its bundle declares."""
    response = await call(client, principal="credit_risk")
    assert response.status_code == 422


async def test_an_unknown_tool_is_a_not_found(client: AsyncClient) -> None:
    response = await call(client, tool="core.write")
    assert response.status_code == 404


async def test_an_unknown_purpose_is_refused(client: AsyncClient) -> None:
    response = await call(client, purpose="MARKETING")
    assert response.status_code == 422


async def test_the_case_scope_is_pinned_to_the_run(client: AsyncClient) -> None:
    """A tool argument naming another case must not widen the reach of a
    repair. The registry scopes the call; this proves the endpoint hands it
    the scope to do that with."""
    response = await call(
        client,
        tool="documents.list",
        args={"case_id": "case_01ARZ3NDEKTSV4RRFFQ69G5FZZ"},
        case_id=CASE,
    )
    assert response.status_code == 403


@pytest.fixture
def writing_tool() -> Iterator[str]:
    """A writing tool, registered only for this test.

    `evidence.request` is the one writing tool today and it is the documented
    exception, so without this the guard against acting during repair would
    have nothing to act on and would never be exercised.
    """
    from cio_tools.registry import registry as live
    from cio_tools.spec import PermittedUse, SideEffect, ToolSpec

    name = "test.activate_financing"
    live.register(
        ToolSpec(
            name=name,
            version="1.0",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_schema={"type": "object"},
            purpose_tags=frozenset({PermittedUse.UNDERWRITING}),
            side_effects=SideEffect.WRITE_PROPOSAL,
            backing_service="test",
            handler=lambda: {"done": True},
        )
    )
    try:
        yield name
    finally:
        live._tools.pop(name, None)


async def test_repair_may_not_take_an_action_on_the_case(client: AsyncClient, writing_tool: str) -> None:
    """docs/06 §8 — repair fetches evidence. Acting on the case is a person's
    to do, and a Challenger that could name an action tool would be able to
    move the case by asking for it."""
    response = await call(client, tool=writing_tool, args={})
    assert response.status_code == 403
    assert "read" in response.json()["error"]["message"]
