"""T-016 — the underwriting workflow skeleton (docs/08 §8.1).

Runs against Temporal's time-skipping test environment with the service calls
replaced by in-process fakes, so the test proves the workflow's control flow:
a blocked case is recorded and routed without being scored, a clean case waits
for a human, and an autonomous route issues a token without waiting.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from workflows.shared import (
    TASK_QUEUE,
    CaseRef,
    CommitteeOutcome,
    DecisionOutcome,
    HumanDecisionSignal,
    ModelOutcome,
    PolicyOutcome,
)
from workflows.underwriting import UnderwriteCase, workflow_id_for

SNAPSHOT = "snap_01JQZK7M8N9P0Q1R2S3T4V5W6X"
CASE = "case_01JQZK7M8N9P0Q1R2S3T4V5W6X"
RECORD = "dr_01JQZK7M8N9P0Q1R2S3T4V5W6X"


class Fakes:
    """In-process stand-ins for the services, recording what was called."""

    def __init__(
        self,
        *,
        blockers: list[str] | None = None,
        route: str = "OFFICER_REVIEW",
        recommendation: str = "APPROVE",
    ) -> None:
        self.blockers = blockers or []
        self.route = route
        self.recommendation = recommendation
        self.calls: list[str] = []
        self.records: list[dict[str, Any]] = []
        self.human_decisions: list[dict[str, Any]] = []
        self.tokens: list[dict[str, Any]] = []

    def build(self) -> list[Any]:
        outer = self

        @activity.defn(name="freeze_snapshot")
        async def freeze_snapshot(case: CaseRef) -> CaseRef:
            outer.calls.append("freeze")
            return CaseRef(
                case_id=case.case_id,
                snapshot_id=SNAPSHOT,
                member_id="M-000042",
                product_code="PF-STD",
                requested_amount="8000.00",
            )

        @activity.defn(name="evaluate_policy")
        async def evaluate_policy(case: CaseRef, inputs: dict[str, Any]) -> PolicyOutcome:
            outer.calls.append("policy")
            return PolicyOutcome(
                policy_result={
                    "blockers": outer.blockers,
                    "rules": [],
                    "required_authority": "CREDIT_OFFICER",
                    "evidence_coverage": 1.0,
                },
                blockers=outer.blockers,
                required_authority="CREDIT_OFFICER",
                evidence_coverage=1.0,
            )

        @activity.defn(name="score_models")
        async def score_models(case: CaseRef) -> ModelOutcome:
            outer.calls.append("models")
            return ModelOutcome(available=False)

        @activity.defn(name="run_committee")
        async def run_committee(case: CaseRef, tier: str) -> CommitteeOutcome:
            outer.calls.append(f"committee:{tier}")
            return CommitteeOutcome(tier=tier, degraded=True)

        @activity.defn(name="decide")
        async def decide(
            case: CaseRef, policy: PolicyOutcome, committee: CommitteeOutcome, model_health: str
        ) -> dict[str, Any]:
            outer.calls.append(f"decide:{model_health}")
            return {
                "decision_record_id": RECORD,
                "snapshot_id": case.snapshot_id,
                "recommendation": outer.recommendation,
                "route": outer.route,
                "required_authority": "CREDIT_OFFICER",
                "tier": committee.tier,
                "weighted_score": None if policy.blockers else 88.0,
            }

        @activity.defn(name="record_decision")
        async def record_decision(case: CaseRef, record: dict[str, Any]) -> DecisionOutcome:
            outer.calls.append("record")
            outer.records.append(record)
            return DecisionOutcome(
                decision_record_id=record["decision_record_id"],
                recommendation=record["recommendation"],
                route=record["route"],
                required_authority=record["required_authority"],
                ledger_entry_id="ent_01JQZK7M8N9P0Q1R2S3T4V5W6X",
            )

        @activity.defn(name="record_human_decision")
        async def record_human_decision(
            case: CaseRef, decision_record_id: str, signal: dict[str, Any]
        ) -> dict[str, Any]:
            outer.calls.append("human")
            outer.human_decisions.append(signal)
            return {
                "human_decision_id": "hd_01JQZK7M8N9P0Q1R2S3T4V5W6X",
                "final_action": signal["final_action"],
            }

        @activity.defn(name="issue_token")
        async def issue_token(
            case: CaseRef, decision_record_id: str, human_decision_id: str | None
        ) -> dict[str, Any]:
            outer.calls.append("token")
            token = {"token_id": "tok_01JQZK7M8N9P0Q1R2S3T4V5W6X", "human_decision_id": human_decision_id}
            outer.tokens.append(token)
            return token

        return [
            freeze_snapshot,
            evaluate_policy,
            score_models,
            run_committee,
            decide,
            record_decision,
            record_human_decision,
            issue_token,
        ]


@pytest_asyncio.fixture(scope="module")
async def env() -> AsyncIterator[WorkflowEnvironment]:
    """One time-skipping test server for the module."""
    environment = await WorkflowEnvironment.start_time_skipping()
    yield environment
    await environment.shutdown()


async def run_workflow(
    env: WorkflowEnvironment,
    fakes: Fakes,
    *,
    signal: HumanDecisionSignal | None = None,
) -> DecisionOutcome:
    client: Client = env.client
    queue = f"{TASK_QUEUE}-{uuid.uuid4()}"
    async with Worker(client, task_queue=queue, workflows=[UnderwriteCase], activities=fakes.build()):
        handle = await client.start_workflow(
            UnderwriteCase.run,
            args=[CaseRef(case_id=CASE), {"member_grade": "B"}],
            id=f"{workflow_id_for(SNAPSHOT)}-{uuid.uuid4()}",
            task_queue=queue,
        )
        if signal is not None:
            await handle.signal(UnderwriteCase.human_decision, signal)
        return await handle.result()


# ---------------------------------------------------------------------------
# the blocked path
# ---------------------------------------------------------------------------
async def test_a_blocked_case_is_recorded_without_being_scored(env: WorkflowEnvironment) -> None:
    """ELG-02 fails: the case must reach the ledger and a human, unscored."""
    fakes = Fakes(blockers=["ELG-02"], recommendation="DECLINE", route="OFFICER_REVIEW")
    outcome = await run_workflow(
        env, fakes, signal=HumanDecisionSignal(actor_id="u-1", role="officer", final_action="DECLINE")
    )

    assert outcome.recommendation == "DECLINE"
    assert outcome.route == "OFFICER_REVIEW"
    assert outcome.ledger_entry_id.startswith("ent_")
    assert "models" not in fakes.calls, "a blocked case must never reach the models"
    assert not any(c.startswith("committee") for c in fakes.calls), (
        "a blocked case must never convene the Council"
    )
    assert fakes.records[0]["weighted_score"] is None
    assert fakes.records[0]["tier"] == "POLICY_ONLY"


async def test_a_blocked_case_still_reaches_a_human(env: WorkflowEnvironment) -> None:
    fakes = Fakes(blockers=["ELG-02"], recommendation="DECLINE")
    outcome = await run_workflow(
        env, fakes, signal=HumanDecisionSignal(actor_id="u-1", role="officer", final_action="DECLINE")
    )
    assert outcome.human_decision_id is not None
    assert outcome.final_action == "DECLINE"
    assert fakes.tokens == [], "a declined case must not be given a token"


# ---------------------------------------------------------------------------
# the normal path
# ---------------------------------------------------------------------------
async def test_a_clean_case_runs_the_full_sequence(env: WorkflowEnvironment) -> None:
    fakes = Fakes()
    await run_workflow(
        env, fakes, signal=HumanDecisionSignal(actor_id="u-1", role="officer", final_action="APPROVE")
    )

    assert fakes.calls[:3] == ["freeze", "policy", "models"]
    assert any(c.startswith("committee") for c in fakes.calls)
    assert "record" in fakes.calls


async def test_an_unavailable_model_forces_a_degraded_run(env: WorkflowEnvironment) -> None:
    """docs/13 §7 — a missing model means STANDARD tier and never autonomous."""
    fakes = Fakes()
    await run_workflow(
        env, fakes, signal=HumanDecisionSignal(actor_id="u-1", role="officer", final_action="APPROVE")
    )

    assert "committee:STANDARD" in fakes.calls
    assert "decide:RED" in fakes.calls


async def test_an_approval_issues_a_token(env: WorkflowEnvironment) -> None:
    fakes = Fakes()
    outcome = await run_workflow(
        env, fakes, signal=HumanDecisionSignal(actor_id="u-1", role="officer", final_action="APPROVE")
    )
    assert outcome.token_id is not None
    assert fakes.tokens[0]["human_decision_id"] == outcome.human_decision_id


async def test_a_decline_issues_no_token(env: WorkflowEnvironment) -> None:
    fakes = Fakes()
    outcome = await run_workflow(
        env, fakes, signal=HumanDecisionSignal(actor_id="u-1", role="officer", final_action="DECLINE")
    )
    assert outcome.token_id is None
    assert fakes.tokens == []


async def test_an_override_is_carried_into_the_human_decision(env: WorkflowEnvironment) -> None:
    fakes = Fakes()
    await run_workflow(
        env,
        fakes,
        signal=HumanDecisionSignal(
            actor_id="u-1",
            role="senior_officer",
            final_action="APPROVE",
            override=True,
            override_reason={"code": "OVR-01", "text": "Employer confirmed the salary."},
        ),
    )

    recorded = fakes.human_decisions[0]
    assert recorded["override"] is True
    assert recorded["override_reason"]["code"] == "OVR-01"


# ---------------------------------------------------------------------------
# the autonomous path
# ---------------------------------------------------------------------------
async def test_an_autonomous_route_issues_a_token_without_waiting(env: WorkflowEnvironment) -> None:
    fakes = Fakes(route="AUTONOMOUS")
    outcome = await run_workflow(env, fakes)  # no signal sent

    assert outcome.route == "AUTONOMOUS"
    assert outcome.token_id is not None
    assert outcome.human_decision_id is None
    assert "human" not in fakes.calls


# ---------------------------------------------------------------------------
# waiting, escalation and idempotency
# ---------------------------------------------------------------------------
async def test_the_workflow_waits_rather_than_deciding_for_itself(env: WorkflowEnvironment) -> None:
    """An unanswered case is escalated, never silently approved."""
    fakes = Fakes()
    client: Client = env.client
    queue = f"{TASK_QUEUE}-{uuid.uuid4()}"

    async with Worker(client, task_queue=queue, workflows=[UnderwriteCase], activities=fakes.build()):
        handle = await client.start_workflow(
            UnderwriteCase.run, args=[CaseRef(case_id=CASE), {}], id=f"wait-{uuid.uuid4()}", task_queue=queue
        )

        async def state() -> str:
            return str(await handle.query(UnderwriteCase.state))

        # wait for the workflow to actually reach the human step
        for _ in range(100):
            if (await state()).startswith("AWAITING_"):
                break
            await asyncio.sleep(0.05)
        assert (await state()) == "AWAITING_OFFICER_REVIEW"

        # let the SLA pass; the workflow escalates and keeps waiting
        await env.sleep(timedelta(hours=30))
        for _ in range(100):
            if (await state()).startswith("ESCALATED_"):
                break
            await asyncio.sleep(0.05)
        assert (await state()) == "ESCALATED_OFFICER_REVIEW"
        assert fakes.human_decisions == [], "the workflow must not decide for itself"

        await handle.signal(
            UnderwriteCase.human_decision,
            HumanDecisionSignal(actor_id="u-late", role="officer", final_action="APPROVE"),
        )
        outcome = await handle.result()

    assert outcome.final_action == "APPROVE"


async def test_the_workflow_id_is_derived_from_the_snapshot() -> None:
    """A repeated start on the same snapshot is a no-op (docs/08 §8.1)."""
    assert workflow_id_for(SNAPSHOT) == f"underwrite-{SNAPSHOT}"
    assert workflow_id_for(SNAPSHOT) == workflow_id_for(SNAPSHOT)


async def test_starting_twice_with_the_same_id_is_refused(env: WorkflowEnvironment) -> None:
    fakes = Fakes()
    client: Client = env.client
    queue = f"{TASK_QUEUE}-{uuid.uuid4()}"
    workflow_id = workflow_id_for(f"{SNAPSHOT}-{uuid.uuid4()}")

    async with Worker(client, task_queue=queue, workflows=[UnderwriteCase], activities=fakes.build()):
        await client.start_workflow(
            UnderwriteCase.run, args=[CaseRef(case_id=CASE), {}], id=workflow_id, task_queue=queue
        )
        with pytest.raises(Exception, match="already"):
            await client.start_workflow(
                UnderwriteCase.run, args=[CaseRef(case_id=CASE), {}], id=workflow_id, task_queue=queue
            )


async def test_the_state_query_tracks_progress(env: WorkflowEnvironment) -> None:
    fakes = Fakes()
    client: Client = env.client
    queue = f"{TASK_QUEUE}-{uuid.uuid4()}"

    async with Worker(client, task_queue=queue, workflows=[UnderwriteCase], activities=fakes.build()):
        handle = await client.start_workflow(
            UnderwriteCase.run, args=[CaseRef(case_id=CASE), {}], id=f"state-{uuid.uuid4()}", task_queue=queue
        )
        await handle.signal(
            UnderwriteCase.human_decision,
            HumanDecisionSignal(actor_id="u-1", role="officer", final_action="APPROVE"),
        )
        await handle.result()
        assert await handle.query(UnderwriteCase.state) == "DONE"
        decision = await handle.query(UnderwriteCase.decision)
        assert decision is not None
        assert decision["token_id"] is not None
