"""T-064 — the early-warning case (docs/08 §8.2).

Everything here follows from one fact: nobody asked the platform to look at
this member. So the case may propose a conversation, may never act against
them, waits for a person, and closes itself when they come back.
"""

from __future__ import annotations

from typing import Any

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError  # noqa: F401
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from workflows.early_warning import EarlyWarningCase, InterventionSignal
from workflows.shared import CaseRef

CASE = CaseRef(
    case_id="case_01JQZK7M8N9P0Q1R2S3T4V5W6X",
    snapshot_id="snap_01JQZK7M8N9P0Q1R2S3T4V5W6X",
    member_id="M-000042",
    product_code="PF-STD",
    requested_amount="8000.00",
)
ALERT = "alert_01JQZK7M8N9P0Q1R2S3T4V5W6X"


class Fakes:
    """In-process stands-ins, recording what the workflow asked for."""

    def __init__(
        self,
        *,
        state: str = "ELEVATED",
        recommendation: str = "INTERVENE",
        actions: list[dict[str, Any]] | None = None,
    ) -> None:
        self.state = state
        self.recommendation = recommendation
        self.actions = (
            actions
            if actions is not None
            else [
                {"action_id": "act_l2", "level": "L2", "type": "OFFICER_OUTREACH"},
                {"action_id": "act_l1", "level": "L1", "type": "REQUEST_DOCUMENT"},
            ]
        )
        self.calls: list[str] = []
        self.executed: list[list[str]] = []

    def build(self) -> list[Any]:
        outer = self

        @activity.defn(name="read_member_watch")
        async def read_member_watch(case: CaseRef) -> dict[str, Any]:
            outer.calls.append("read")
            return {
                "state": outer.state,
                "since": "2026-08-01",
                "rule": "CHANGE_POINT_CORROBORATED",
                "reason": "payment timing began drifting",
                "transitions": [],
                "features": {"days_to_pay_median_30d": 9.0},
                "scores": [{"horizon_days": 30, "probability": 0.32}],
                "available": True,
            }

        @activity.defn(name="run_longitudinal_council")
        async def run_longitudinal_council(
            case: CaseRef, alert_id: str, watch: dict[str, Any]
        ) -> dict[str, Any]:
            outer.calls.append("council")
            return {
                "decision_record_id": "dr_01JQZK7M8N9P0Q1R2S3T4V5W6X",
                "recommendation": outer.recommendation,
                "route": "OFFICER_REVIEW",
                "proposed_actions": outer.actions,
                "degraded": False,
                "p30": 0.32,
            }

        @activity.defn(name="carry_out_intervention")
        async def carry_out_intervention(
            case: CaseRef, decision_record_id: str, action_ids: list[str], signal: dict[str, Any]
        ) -> dict[str, Any]:
            outer.calls.append("execute")
            outer.executed.append(list(action_ids))
            return {"executed": action_ids, "failed": []}

        return [read_member_watch, run_longitudinal_council, carry_out_intervention]


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        yield environment


async def run_case(
    env: WorkflowEnvironment,
    fakes: Fakes,
    *,
    signal: InterventionSignal | None = None,
    recover: bool = False,
) -> Any:
    async with Worker(
        env.client,
        task_queue="cio-lmi-test",
        workflows=[EarlyWarningCase],
        activities=fakes.build(),
    ):
        handle = await env.client.start_workflow(
            EarlyWarningCase.run,
            args=[CASE, ALERT, 0],
            id=f"ew-{CASE.member_id}-{id(fakes)}",
            task_queue="cio-lmi-test",
        )
        if signal is not None:
            await handle.signal(EarlyWarningCase.intervention_decided, signal)
        if recover:
            await handle.signal(EarlyWarningCase.member_recovered)
        return await handle.result()


# ---------------------------------------------------------------------------
# a member who does not need a case
# ---------------------------------------------------------------------------
async def test_a_member_who_recovered_before_pickup_needs_no_case(
    env: WorkflowEnvironment,
) -> None:
    fakes = Fakes(state="STABLE")
    outcome = await run_case(env, fakes)

    assert outcome.closed
    assert "STABLE" in (outcome.close_reason or "")
    assert "council" not in fakes.calls, "a Council was convened about nothing"


# ---------------------------------------------------------------------------
# a member who does
# ---------------------------------------------------------------------------
async def test_an_elevated_member_reaches_the_council(env: WorkflowEnvironment) -> None:
    fakes = Fakes()
    outcome = await run_case(
        env,
        fakes,
        signal=InterventionSignal(actor_id="u-1", role="officer", approved=["act_l2"]),
        recover=True,
    )
    assert "council" in fakes.calls
    assert outcome.recommendation == "INTERVENE"


async def test_an_l3_action_never_survives_the_workflow(env: WorkflowEnvironment) -> None:
    """The synthesizer drops them. This is the second check, because a workflow
    that trusted the record would execute whatever reached it."""
    fakes = Fakes(
        actions=[
            {"action_id": "act_l3", "level": "L3", "type": "RESTRUCTURE"},
            {"action_id": "act_l2", "level": "L2", "type": "OFFICER_OUTREACH"},
        ]
    )
    outcome = await run_case(
        env,
        fakes,
        signal=InterventionSignal(actor_id="u-1", role="officer", approved=["act_l2"]),
        recover=True,
    )
    assert [action["level"] for action in outcome.proposed_actions] == ["L2"]


async def test_only_what_a_person_approved_is_carried_out(env: WorkflowEnvironment) -> None:
    fakes = Fakes()
    await run_case(
        env,
        fakes,
        signal=InterventionSignal(actor_id="u-1", role="officer", approved=["act_l2"], declined=["act_l1"]),
        recover=True,
    )
    assert fakes.executed == [["act_l2"]]


async def test_an_l1_action_still_waits_for_a_person(env: WorkflowEnvironment) -> None:
    """It could auto-execute under a permissive dial. It does not: the first
    contact with somebody who has not been told they are being watched is a
    person's to make."""
    fakes = Fakes(actions=[{"action_id": "act_l1", "level": "L1", "type": "REQUEST_DOCUMENT"}])
    outcome = await run_case(
        env,
        fakes,
        signal=InterventionSignal(actor_id="u-1", role="officer", approved=[]),
        recover=True,
    )
    assert fakes.executed == []
    assert outcome.approved_actions == []


async def test_declining_everything_carries_nothing_out(env: WorkflowEnvironment) -> None:
    fakes = Fakes()
    await run_case(
        env,
        fakes,
        signal=InterventionSignal(actor_id="u-1", role="officer", declined=["act_l2", "act_l1"]),
        recover=True,
    )
    assert fakes.executed == []


async def test_a_member_who_recovers_closes_the_case(env: WorkflowEnvironment) -> None:
    """A member who has recovered should not receive an outreach the platform
    queued a week ago."""
    fakes = Fakes(recommendation="MONITOR")
    outcome = await run_case(env, fakes, recover=True)

    assert outcome.closed
    assert outcome.close_reason == "the member returned to STABLE"


async def test_monitoring_asks_nobody_for_anything(env: WorkflowEnvironment) -> None:
    fakes = Fakes(recommendation="MONITOR")
    outcome = await run_case(env, fakes, recover=True)
    assert outcome.approved_actions == []
    assert "execute" not in fakes.calls


async def test_the_outcome_names_the_alert_it_came_from(env: WorkflowEnvironment) -> None:
    fakes = Fakes(recommendation="DE_ESCALATE")
    outcome = await run_case(env, fakes, recover=True)
    assert outcome.alert_id == ALERT
    assert outcome.member_id == CASE.member_id
