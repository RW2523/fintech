"""T-050 — Tier 2 repair and revise (docs/06 §8).

The Challenger names what is missing, the orchestrator fetches it, the agents
whose ground the gap is on answer again, and the Challenger sees the result.
Everything here is about that cycle: that it happens, that it is bounded, and
that a run which cannot repair still reaches a decision.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from app.clients import UpstreamError
from app.orchestrate import COUNCIL
from tests.conftest import opinion


def gap(**overrides: Any) -> dict[str, Any]:
    """One unresolved item from the Challenger."""
    body = {
        "question": "the payslip has not been reconciled against the deduction file",
        "requested_tool": "reconciliation.get",
        "family": "CAPACITY",
        "blocking": True,
    }
    body.update(overrides)
    return body


async def extended(client: AsyncClient, snapshot: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """Start a run that qualifies for Tier 2."""
    body = {
        "snapshot": snapshot,
        # Both keys the Synthesizer reads. `blockers` alone is a shape the
        # evaluator never returns.
        "policy_result": {"blockers": [], "rules": [], "evidence_coverage": 1.0},
        "top_band": True,
        **overrides,
    }
    return (await client.post("/committee/runs", json=body)).json()


def challenger_wants(fake: Any, *gaps: dict[str, Any], then_clean: bool = True) -> None:
    """Script the Challenger: a reservation first, then satisfied or not."""
    raised = opinion("challenger", stance="REVIEW", unresolved=list(gaps))
    settled = opinion("challenger", stance="REVIEW", unresolved=[] if then_clean else list(gaps))
    fake.sequences["challenger"] = [raised, settled, settled, settled]


# ---------------------------------------------------------------------------
# the cycle
# ---------------------------------------------------------------------------
async def test_a_named_tool_is_called_by_the_orchestrator_not_the_agent(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    """docs/06 §8 — the orchestrator repairs, with the workflow as principal.

    An agent that could fill its own gap would be deciding what counts as
    evidence about its own case.
    """
    challenger_wants(fake, gap())
    fake.tool_results["reconciliation.get"] = {
        "result": {"matched": True},
        "evidence_ids": ["ev_01ARZ3NDEKTSV4RRFFQ69G5FAW"],
        "call_id": "tc_000001",
    }

    body = await extended(client, snapshot)

    called = [c[1] for c in fake.calls if c[0] == "call_tool"]
    assert [c["tool"] for c in called] == ["reconciliation.get"]
    assert called[0]["principal"] == "workflow"
    assert called[0]["case_id"] == snapshot["case_id"]
    assert body["repair_loops"] == 1


async def test_the_repaired_evidence_reaches_the_agents_that_revise(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    """A revision is the agent reconsidering with more evidence, not less."""
    challenger_wants(fake, gap())
    fake.tool_results["reconciliation.get"] = {
        "result": {"matched": False, "difference": 240.0},
        "evidence_ids": ["ev_01ARZ3NDEKTSV4RRFFQ69G5FAW"],
        "call_id": "tc_000001",
    }

    await extended(client, snapshot)

    revisions = [c[1] for c in fake.calls if c[0] == "invoke" and c[1]["round"] == "REVISE"]
    assert revisions, "no agent was asked to revise"
    fetched = [
        entry
        for call in revisions
        for entry in call["tool_results"]
        if entry.get("tool") == "reconciliation.get"
    ]
    assert fetched, "the repaired evidence never reached the revising agent"
    assert fetched[0]["result"] == {"matched": False, "difference": 240.0}
    assert fetched[0]["round"] == "REPAIR"


async def test_the_revision_goes_to_the_agent_whose_ground_the_gap_is_on(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    challenger_wants(fake, gap(family="CONDUCT"))
    fake.tool_results["reconciliation.get"] = {"result": {}, "evidence_ids": [], "call_id": "tc_1"}

    await extended(client, snapshot)

    revised = {c[1]["agent_id"] for c in fake.calls if c[0] == "invoke" and c[1]["round"] == "REVISE"}
    assert revised == {"credit_risk"}


async def test_a_gap_naming_no_family_goes_to_everybody(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    """The orchestrator cannot tell whose ground it is on, and guessing wrong
    means the revision never reaches the agent whose answer would change."""
    challenger_wants(fake, gap(family=None))
    fake.tool_results["reconciliation.get"] = {"result": {}, "evidence_ids": [], "call_id": "tc_1"}

    await extended(client, snapshot)

    revised = {c[1]["agent_id"] for c in fake.calls if c[0] == "invoke" and c[1]["round"] == "REVISE"}
    assert revised == set(COUNCIL)


async def test_the_challenger_sees_the_repair_and_can_withdraw_its_reservation(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    """Without a second CHALLENGE, a reservation the repair answered stands."""
    challenger_wants(fake, gap(), then_clean=True)
    fake.tool_results["reconciliation.get"] = {"result": {}, "evidence_ids": [], "call_id": "tc_1"}

    body = await extended(client, snapshot)

    assert body["rounds"] == [
        "ASSESS",
        "CHALLENGE",
        "REPAIR",
        "REVISE",
        "CHALLENGE",
        "SYNTHESIZE",
        "NARRATE",
    ]
    assert body["repair_loops"] == 1, "a satisfied Challenger must not trigger another loop"


async def test_the_revising_agent_is_given_the_prior_round(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    """docs/06 §3 — a revision without the prior round is not a revision."""
    challenger_wants(fake, gap())
    fake.tool_results["reconciliation.get"] = {"result": {}, "evidence_ids": [], "call_id": "tc_1"}

    await extended(client, snapshot)

    revision = next(c[1] for c in fake.calls if c[0] == "invoke" and c[1]["round"] == "REVISE")
    assert revision["prior_opinions"], "the agent was asked to revise nothing"


# ---------------------------------------------------------------------------
# the bound
# ---------------------------------------------------------------------------
async def test_repair_stops_after_two_loops(client: AsyncClient, snapshot: Any, fake: Any) -> None:
    """An unsatisfiable Challenger must not hold the case open forever."""
    challenger_wants(fake, gap(), then_clean=False)
    fake.tool_results["reconciliation.get"] = {"result": {}, "evidence_ids": [], "call_id": "tc_1"}

    body = await extended(client, snapshot)

    assert body["repair_loops"] == 2
    assert body["rounds"] == [
        "ASSESS",
        "CHALLENGE",
        "REPAIR",
        "REVISE",
        "CHALLENGE",
        "REPAIR",
        "REVISE",
        "CHALLENGE",
        "SYNTHESIZE",
        "NARRATE",
    ]
    assert body["state"] == "DONE", "the bound must end the loop, not the run"


async def test_a_standing_reservation_after_the_bound_still_reaches_a_decision(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    challenger_wants(fake, gap(), then_clean=False)
    fake.tool_results["reconciliation.get"] = {"result": {}, "evidence_ids": [], "call_id": "tc_1"}

    body = await extended(client, snapshot)

    assert body["decision_record_id"]
    assert "SYNTHESIZE" in body["rounds"]


# ---------------------------------------------------------------------------
# what cannot be repaired
# ---------------------------------------------------------------------------
async def test_evidence_no_tool_can_fetch_becomes_a_proposal_for_a_person(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    """docs/06 §8 — L1 is a proposal, never an action."""
    challenger_wants(
        fake,
        gap(requested_tool=None, requested_evidence="PAYSLIP_LATEST_3", blocking=True),
    )

    body = await extended(client, snapshot)

    proposals = body["proposed_actions"]
    assert len(proposals) == 1
    assert proposals[0]["type"] == "REQUEST_DOCUMENT"
    assert proposals[0]["level"] == "L1"
    assert proposals[0]["state"] == "PROPOSED"
    assert proposals[0]["requires"] == "OFFICER"
    assert proposals[0]["parameters"]["blocking"] is True
    assert not [c for c in fake.calls if c[0] == "call_tool"]


async def test_the_proposal_reaches_the_synthesizer(client: AsyncClient, snapshot: Any, fake: Any) -> None:
    challenger_wants(fake, gap(requested_tool=None, requested_evidence="BANK_STATEMENT_6M"))

    await extended(client, snapshot)

    synthesis = next(c[1] for c in fake.calls if c[0] == "synthesize")
    assert [p["type"] for p in synthesis["proposed_actions"]] == ["REQUEST_DOCUMENT"]


async def test_a_tool_that_cannot_be_reached_is_recorded_rather_than_dropped(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    """The next round must tell "we looked and found nothing" from "we never
    looked"."""
    challenger_wants(fake, gap(), then_clean=False)
    fake.tool_results["reconciliation.get"] = UpstreamError("the service refused")

    body = await extended(client, snapshot)

    failures = [note for note in body["repairs"] if not note["ok"]]
    assert failures, "an unreachable tool left no trace"
    assert failures[0]["tool"] == "reconciliation.get"
    assert body["state"] == "DONE"


# ---------------------------------------------------------------------------
# what the Synthesizer is given
# ---------------------------------------------------------------------------
async def test_a_revised_opinion_supersedes_the_one_it_revised(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    """Passing both would count one agent twice in the disagreement measure."""
    challenger_wants(fake, gap(family="CONDUCT"))
    fake.tool_results["reconciliation.get"] = {"result": {}, "evidence_ids": [], "call_id": "tc_1"}
    fake.sequences["credit_risk"] = [
        opinion("credit_risk", stance="SUPPORT"),
        opinion("credit_risk", stance="LEAN_OPPOSE"),
    ]

    await extended(client, snapshot)

    synthesis = next(c[1] for c in fake.calls if c[0] == "synthesize")
    risk = [o for o in synthesis["opinions"] if o["agent_id"] == "credit_risk"]
    assert len(risk) == 1
    assert risk[0]["stance"] == "LEAN_OPPOSE"


async def test_every_opinion_written_is_still_on_the_run(
    client: AsyncClient, snapshot: Any, fake: Any
) -> None:
    """The Synthesizer reads what stands; the record keeps the whole
    deliberation, because reconstructing it is the point of the ledger."""
    challenger_wants(fake, gap(family="CONDUCT"))
    fake.tool_results["reconciliation.get"] = {"result": {}, "evidence_ids": [], "call_id": "tc_1"}
    fake.sequences["credit_risk"] = [
        opinion("credit_risk", stance="SUPPORT"),
        opinion("credit_risk", stance="LEAN_OPPOSE"),
    ]

    body = await extended(client, snapshot)

    synthesis = next(c[1] for c in fake.calls if c[0] == "synthesize")
    assert len(body["opinions"]) > len(synthesis["opinions"])


# ---------------------------------------------------------------------------
# tiers below Extended
# ---------------------------------------------------------------------------
async def test_a_standard_run_does_not_repair(client: AsyncClient, snapshot: Any, fake: Any) -> None:
    """docs/06 §8 — REPAIR is Tier 2 only. A Standard case with a reservation
    routes to a person instead, which is cheaper and just as safe."""
    challenger_wants(fake, gap(), then_clean=False)
    body = await extended(
        client,
        snapshot,
        top_band=False,
        policy_result={
            "blockers": ["AFF-01"],
            "rules": [{"rule_id": "AFF-01", "result": "FAIL", "on_fail": "POLICY_EXCEPTION_OR_DECLINE"}],
            "evidence_coverage": 1.0,
        },
    )

    assert body["tier"] == "STANDARD"
    assert "REPAIR" not in body["rounds"]
    assert not [c for c in fake.calls if c[0] == "call_tool"]
