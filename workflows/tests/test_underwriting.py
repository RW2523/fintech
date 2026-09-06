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
    DocumentOutcome,
    FeatureOutcome,
    HumanDecisionSignal,
    ModelOutcome,
    PolicyOutcome,
)
from workflows.underwriting import UnderwriteCase, workflow_id_for

SNAPSHOT = "snap_01JQZK7M8N9P0Q1R2S3T4V5W6X"
CASE = "case_01JQZK7M8N9P0Q1R2S3T4V5W6X"
RECORD = "dr_01JQZK7M8N9P0Q1R2S3T4V5W6X"
FEATURE_SNAPSHOT = "fs_01JQZK7M8N9P0Q1R2S3T4V5W6X"


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
        # Each step can be made to fail on its own, because the workflow's job
        # is to keep going and mark the run rather than to stop.
        self.documents_available = True
        self.features_available = True
        self.models_available = True
        self.committee_degraded = False
        self.tier = "STANDARD"
        self.documents: list[dict[str, Any]] = []
        self.findings: list[dict[str, Any]] = []
        self.risk: dict[str, Any] = {"model_run_id": "mr_test", "champion": {"grade": "A"}}
        self.fraud: dict[str, Any] = {"level": "LOW", "findings": []}
        self.committee_requests: list[dict[str, Any]] = []
        self.calls: list[str] = []
        self.records: list[dict[str, Any]] = []
        self.human_decisions: list[dict[str, Any]] = []
        self.tokens: list[dict[str, Any]] = []
        self.executions: list[dict[str, Any]] = []
        #: What the core does with the approval. FAILED leaves the case
        #: pending, which is a state the workflow has to survive.
        self.execution_state = "EXECUTED"

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

        @activity.defn(name="gather_documents")
        async def gather_documents(case: CaseRef) -> DocumentOutcome:
            outer.calls.append("documents")
            return DocumentOutcome(
                documents=outer.documents, findings=outer.findings, available=outer.documents_available
            )

        @activity.defn(name="compute_features")
        async def compute_features(case: CaseRef) -> FeatureOutcome:
            outer.calls.append("features")
            return FeatureOutcome(snapshot_id=FEATURE_SNAPSHOT, available=outer.features_available)

        @activity.defn(name="score_models")
        async def score_models(case: CaseRef, feature_snapshot_id: str = "") -> ModelOutcome:
            outer.calls.append(f"models:{feature_snapshot_id or 'none'}")
            return ModelOutcome(available=outer.models_available, risk=outer.risk, fraud=outer.fraud)

        @activity.defn(name="run_committee")
        async def run_committee(case: CaseRef, request: dict[str, Any]) -> CommitteeOutcome:
            outer.calls.append(f"committee:{request.get('fraud_level')}")
            outer.committee_requests.append(request)
            return CommitteeOutcome(tier=outer.tier, degraded=outer.committee_degraded)

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

        @activity.defn(name="execute_action")
        async def execute_action(
            case: CaseRef,
            decision_record_id: str,
            token_id: str,
            human_decision_id: str | None,
        ) -> dict[str, Any]:
            outer.calls.append("execute")
            result = {
                "action_id": f"act_{case.snapshot_id[5:]}",
                "state": outer.execution_state,
                "core_refs": [{"system": "core", "kind": "account", "id": "A-000001"}],
            }
            outer.executions.append(result)
            return result

        return [
            freeze_snapshot,
            evaluate_policy,
            gather_documents,
            compute_features,
            score_models,
            run_committee,
            decide,
            execute_action,
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

    assert fakes.calls[:2] == ["freeze", "policy"]
    # Documents and features are gathered concurrently, so their order is not
    # fixed; what matters is that both precede the models.
    assert set(fakes.calls[2:4]) == {"documents", "features"}
    assert any(c.startswith("models:") for c in fakes.calls)
    assert any(c.startswith("committee") for c in fakes.calls)
    assert "record" in fakes.calls


async def test_the_models_score_the_frozen_feature_snapshot(env: WorkflowEnvironment) -> None:
    """Risk and fraud must reason about the same inputs, so the snapshot is
    frozen once before either runs and the decision cites one, not two."""
    fakes = Fakes()
    await run_workflow(
        env, fakes, signal=HumanDecisionSignal(actor_id="u-1", role="officer", final_action="APPROVE")
    )
    assert f"models:{FEATURE_SNAPSHOT}" in fakes.calls


async def test_the_committee_is_told_what_it_needs_to_choose_a_tier(env: WorkflowEnvironment) -> None:
    """The workflow gathers; the committee decides the tier, so the rule has
    one implementation rather than two that can disagree."""
    fakes = Fakes()
    fakes.fraud = {"level": "HIGH", "findings": [{"code": "INT-05"}]}
    fakes.findings = [{"code": "INT-08", "severity": "CRITICAL"}]
    await run_workflow(
        env, fakes, signal=HumanDecisionSignal(actor_id="u-1", role="officer", final_action="APPROVE")
    )

    request = fakes.committee_requests[0]
    assert request["fraud_level"] == "HIGH"
    assert request["identity_mismatch"] is True
    assert request["policy_result"] is not None
    assert request["snapshot"]["feature_snapshot_id"] == FEATURE_SNAPSHOT


async def test_an_unavailable_model_forces_a_degraded_run(env: WorkflowEnvironment) -> None:
    """docs/13 §7 — a model that did not run is not a model that found nothing."""
    fakes = Fakes()
    fakes.models_available = False
    await run_workflow(
        env, fakes, signal=HumanDecisionSignal(actor_id="u-1", role="officer", final_action="APPROVE")
    )
    assert "decide:RED" in fakes.calls


async def test_an_unreadable_case_file_forces_a_degraded_run(env: WorkflowEnvironment) -> None:
    """A case file that could not be read is not an empty case file."""
    fakes = Fakes()
    fakes.documents_available = False
    await run_workflow(
        env, fakes, signal=HumanDecisionSignal(actor_id="u-1", role="officer", final_action="APPROVE")
    )
    assert "decide:RED" in fakes.calls


async def test_an_unavailable_feature_snapshot_forces_a_degraded_run(env: WorkflowEnvironment) -> None:
    fakes = Fakes()
    fakes.features_available = False
    await run_workflow(
        env, fakes, signal=HumanDecisionSignal(actor_id="u-1", role="officer", final_action="APPROVE")
    )
    assert "decide:RED" in fakes.calls


async def test_everything_working_keeps_the_run_healthy(env: WorkflowEnvironment) -> None:
    fakes = Fakes()
    await run_workflow(
        env, fakes, signal=HumanDecisionSignal(actor_id="u-1", role="officer", final_action="APPROVE")
    )
    assert "decide:GREEN" in fakes.calls


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


# ---------------------------------------------------------------------------
# T-052 — the approval becomes a facility, or the case stays pending
# ---------------------------------------------------------------------------
async def test_an_approval_is_carried_out(env: WorkflowEnvironment) -> None:
    fakes = Fakes()
    outcome = await run_workflow(
        env, fakes, signal=HumanDecisionSignal(actor_id="u-1", role="officer", final_action="APPROVE")
    )
    assert "execute" in fakes.calls
    assert outcome.execution_state == "EXECUTED"
    assert outcome.action_id


async def test_a_decline_is_not_carried_out(env: WorkflowEnvironment) -> None:
    """Nothing is written for a case that was refused, and nothing should be."""
    fakes = Fakes()
    await run_workflow(
        env, fakes, signal=HumanDecisionSignal(actor_id="u-1", role="officer", final_action="DECLINE")
    )
    assert "execute" not in fakes.calls


async def test_an_autonomous_case_is_carried_out_without_waiting(env: WorkflowEnvironment) -> None:
    fakes = Fakes(route="AUTONOMOUS")
    outcome = await run_workflow(env, fakes)
    assert fakes.calls.count("execute") == 1
    assert outcome.execution_state == "EXECUTED"


async def test_a_core_refusal_leaves_the_workflow_finished_and_the_case_pending(
    env: WorkflowEnvironment,
) -> None:
    """A core that refuses is not a workflow failure. Failing here would lose
    the approval and make a person issue it again for a write that timed out."""
    fakes = Fakes(route="AUTONOMOUS")
    fakes.execution_state = "FAILED"

    outcome = await run_workflow(env, fakes)
    assert outcome.execution_state == "FAILED"
    assert outcome.token_id, "the approval is still on the record"
