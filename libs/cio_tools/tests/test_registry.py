"""T-006 — the registry enforces grants, scope, budgets, masking and evidence.

These are the guarantees CLAUDE.md §2.5 rests on: an agent reaches nothing it
was not granted, cannot widen its own scope, and cites only evidence a tool
actually produced.
"""

from __future__ import annotations

import dataclasses

import pytest

from cio_common.errors import ValidationFailed
from cio_tools import (
    BudgetExceeded,
    CallBudget,
    Grant,
    GrantRegistry,
    PermittedUse,
    SideEffect,
    ToolContext,
    ToolDenied,
    ToolRegistry,
    ToolSpec,
)

UNDERWRITING = PermittedUse.UNDERWRITING
COLLECTIONS = PermittedUse.COLLECTIONS
ANALYTICS = PermittedUse.ANALYTICS


# ---------------------------------------------------------------------------
# grants
# ---------------------------------------------------------------------------
async def test_a_granted_tool_returns_its_result(reg: ToolRegistry, ctx: ToolContext) -> None:
    result = await reg.call("risk.score", {"snapshot_id": "snap_1"}, ctx)
    assert result["grade"] == "B"
    assert result["pd_12m"] == 0.021


async def test_an_ungranted_tool_is_denied(reg: ToolRegistry, ctx: ToolContext) -> None:
    """The headline guarantee: no grant, no call."""
    fraud_ctx = dataclasses.replace(ctx, agent_id="fraud_integrity")
    with pytest.raises(ToolDenied) as exc:
        await reg.call("risk.score", {"snapshot_id": "snap_1"}, fraud_ctx)
    assert exc.value.reason == "NO_GRANT"
    assert exc.value.tool == "risk.score"


async def test_a_denial_is_audited(reg: ToolRegistry, ctx: ToolContext) -> None:
    fraud_ctx = dataclasses.replace(ctx, agent_id="fraud_integrity")
    with pytest.raises(ToolDenied):
        await reg.call("risk.score", {"snapshot_id": "snap_1"}, fraud_ctx)

    record = reg.invocations[-1]
    assert record.ok is False
    assert record.denial_reason == "NO_GRANT"
    assert record.agent_id == "fraud_integrity"
    assert record.run_id == ctx.run_id


async def test_unknown_tools_raise_a_helpful_error(reg: ToolRegistry, ctx: ToolContext) -> None:
    with pytest.raises(KeyError, match="unknown tool"):
        await reg.call("nope.get", {}, ctx)


async def test_a_tool_outside_the_calls_purpose_is_denied(reg: ToolRegistry, ctx: ToolContext) -> None:
    """risk.score is an underwriting tool; a collections run may not use it."""
    collections_ctx = dataclasses.replace(ctx, purpose=COLLECTIONS)
    with pytest.raises(ToolDenied) as exc:
        await reg.call("risk.score", {"snapshot_id": "snap_1"}, collections_ctx)
    assert exc.value.reason == "PURPOSE_NOT_PERMITTED"


async def test_a_writing_tool_is_refused_in_an_analytics_context() -> None:
    """Analytics reads history; it never records proposals (docs/06 §4)."""
    registry = ToolRegistry(GrantRegistry([Grant("manager_copilot", "note.write", 1)]))

    async def note_write(text: str) -> dict:
        return {"recorded": True}

    registry.register(
        ToolSpec(
            name="note.write",
            version="1.0",
            handler=note_write,
            input_schema={"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"recorded": {"type": "boolean"}}},
            purpose_tags=frozenset({UNDERWRITING, ANALYTICS}),
            side_effects=SideEffect.WRITE_PROPOSAL,
            backing_service="committee",
        )
    )

    ctx = ToolContext(agent_id="manager_copilot", run_id="run_1", purpose=ANALYTICS)
    with pytest.raises(ToolDenied) as exc:
        await registry.call("note.write", {"text": "x"}, ctx)
    assert exc.value.reason == "WRITE_IN_READ_CONTEXT"


async def test_a_write_proposal_tool_works_for_its_declared_purposes(reg: ToolRegistry) -> None:
    planner = ToolContext(agent_id="intervention_planner", run_id="run_1", purpose=COLLECTIONS)
    assert (await reg.call("evidence.request", {"question": "confirm salary"}, planner))["recorded"] is True


# ---------------------------------------------------------------------------
# scope
# ---------------------------------------------------------------------------
async def test_arguments_are_pinned_to_the_run_scope(reg: ToolRegistry, ctx: ToolContext) -> None:
    """An omitted member_id is filled from the run, not left to the model."""
    result = await reg.call("history.get", {"member_id": ctx.member_id}, ctx)
    assert result["member_id"] == "M-000042"


async def test_calling_outside_the_run_scope_is_denied(reg: ToolRegistry, ctx: ToolContext) -> None:
    """An agent must not reach another member's data by passing their id."""
    with pytest.raises(ToolDenied) as exc:
        await reg.call("history.get", {"member_id": "M-999999"}, ctx)
    assert exc.value.reason == "OUT_OF_SCOPE"


# ---------------------------------------------------------------------------
# budgets
# ---------------------------------------------------------------------------
async def test_the_call_budget_is_enforced_per_tool(reg: ToolRegistry, ctx: ToolContext) -> None:
    await reg.call("risk.score", {"snapshot_id": "snap_1"}, ctx)
    with pytest.raises(BudgetExceeded) as exc:
        await reg.call("risk.score", {"snapshot_id": "snap_1"}, ctx)
    assert exc.value.limit == 1
    assert exc.value.reason == "BUDGET_EXCEEDED"


async def test_budgets_are_independent_per_tool(reg: ToolRegistry, ctx: ToolContext) -> None:
    await reg.call("risk.score", {"snapshot_id": "snap_1"}, ctx)
    await reg.call("history.get", {"member_id": ctx.member_id}, ctx)
    await reg.call("history.get", {"member_id": ctx.member_id}, ctx)
    assert ctx.budget.spent("credit_risk", "history.get") == 2


async def test_a_run_wide_ceiling_stops_a_runaway_agent(reg: ToolRegistry) -> None:
    ctx = ToolContext(
        agent_id="credit_risk",
        run_id="run_1",
        purpose=UNDERWRITING,
        member_id="M-000042",
        budget=CallBudget(max_total=2),
    )
    await reg.call("risk.score", {"snapshot_id": "snap_1"}, ctx)
    await reg.call("history.get", {"member_id": "M-000042"}, ctx)
    with pytest.raises(BudgetExceeded, match="run exhausted"):
        await reg.call("history.get", {"member_id": "M-000042"}, ctx)


async def test_budgets_do_not_leak_between_runs(reg: ToolRegistry, ctx: ToolContext) -> None:
    await reg.call("risk.score", {"snapshot_id": "snap_1"}, ctx)
    fresh = dataclasses.replace(ctx, run_id="run_2", budget=CallBudget())
    await reg.call("risk.score", {"snapshot_id": "snap_1"}, fresh)


async def test_a_scope_violation_still_costs_budget(reg: ToolRegistry, ctx: ToolContext) -> None:
    """Deliberate: otherwise an agent could probe other members' ids for free."""
    with pytest.raises(ToolDenied):
        await reg.call("history.get", {"member_id": "M-999999"}, ctx)
    assert ctx.budget.spent("credit_risk", "history.get") == 1


# ---------------------------------------------------------------------------
# schemas
# ---------------------------------------------------------------------------
async def test_input_is_validated_against_the_tool_schema(reg: ToolRegistry, ctx: ToolContext) -> None:
    with pytest.raises(ValidationFailed, match="input"):
        await reg.call("risk.score", {"snapshot": "snap_1"}, ctx)


async def test_output_is_validated_against_the_tool_schema(reg: ToolRegistry) -> None:
    registry = ToolRegistry(GrantRegistry([Grant("a", "broken.tool", 1)]))

    async def broken() -> dict:
        return {"unexpected": True}

    registry.register(
        ToolSpec(
            name="broken.tool",
            version="1.0",
            handler=broken,
            input_schema={"type": "object", "properties": {}},
            output_schema={
                "type": "object",
                "required": ["expected"],
                "properties": {"expected": {"type": "string"}},
            },
            purpose_tags=frozenset({UNDERWRITING}),
            side_effects=SideEffect.READ,
            backing_service="test",
        )
    )
    ctx = ToolContext(agent_id="a", run_id="run_1", purpose=UNDERWRITING)
    with pytest.raises(ValidationFailed, match="output"):
        await registry.call("broken.tool", {}, ctx)
