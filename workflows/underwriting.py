"""The origination workflow (docs/01 §3, docs/08 §8.1).

    freeze -> documents + features -> policy gates
      -> [blocked?] record and route to a human
      -> risk + fraud -> tier -> Council -> synthesize
      -> record -> route -> autonomous token, or wait for a human
      -> execute -> ledger

Committee and model steps are placeholders until P3 and P4. A placeholder run is
marked degraded and forced to a human route: a partial assessment must never
look like a complete one (CLAUDE.md §2.7).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from workflows import activities
    from workflows.shared import (
        CaseRef,
        CommitteeOutcome,
        DecisionOutcome,
        HumanDecisionSignal,
        ModelOutcome,
        PolicyOutcome,
    )

__all__ = ["UnderwriteCase", "workflow_id_for"]

#: Routes that end the workflow without waiting for anyone.
_TERMINAL_ROUTES = frozenset({"AUTONOMOUS"})

#: How long to wait for a human before escalating. The workflow keeps waiting
#: afterwards: an unanswered case is escalated, never silently decided.
_HUMAN_SLA = {
    "OFFICER_REVIEW": timedelta(hours=24),
    "SENIOR_REVIEW": timedelta(hours=48),
    "ENHANCED_ASSESSMENT": timedelta(hours=72),
    "COMMITTEE": timedelta(days=7),
    "COMPLIANCE": timedelta(hours=48),
    "MANUAL_FALLBACK": timedelta(hours=24),
}

_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3)
_SHORT = timedelta(seconds=30)
_LONG = timedelta(minutes=5)


def workflow_id_for(snapshot_id: str) -> str:
    """One workflow per snapshot, so a repeated start is a no-op."""
    return f"underwrite-{snapshot_id}"


@workflow.defn(name="UnderwriteCase")
class UnderwriteCase:
    """Underwrite one case from a frozen snapshot to a recorded decision."""

    def __init__(self) -> None:
        self._human: HumanDecisionSignal | None = None
        self._state = "CREATED"
        self._decision: DecisionOutcome | None = None

    # -- signals and queries ----------------------------------------------
    @workflow.signal(name="human_decision")
    async def human_decision(self, signal: HumanDecisionSignal) -> None:
        """An officer's decision arrives here (docs/08 §8.1)."""
        self._human = signal

    @workflow.query(name="state")
    def state(self) -> str:
        return self._state

    @workflow.query(name="decision")
    def decision(self) -> dict[str, Any] | None:
        return asdict(self._decision) if self._decision else None

    # -- the run ------------------------------------------------------------
    @workflow.run
    async def run(self, case: CaseRef, inputs: dict[str, Any]) -> DecisionOutcome:
        self._state = "FREEZING"
        case = await workflow.execute_activity(
            activities.freeze_snapshot, case, start_to_close_timeout=_SHORT, retry_policy=_RETRY
        )

        self._state = "POLICY"
        policy: PolicyOutcome = await workflow.execute_activity(
            activities.evaluate_policy,
            args=[case, inputs],
            start_to_close_timeout=_SHORT,
            retry_policy=_RETRY,
        )

        # A blocked case is recorded and routed without ever being scored.
        if policy.blockers:
            self._state = "POLICY_STOP"
            return await self._record_and_settle(
                case, policy, CommitteeOutcome(tier="POLICY_ONLY", degraded=True), model_health="GREEN"
            )

        self._state = "MODELS"
        models: ModelOutcome = await workflow.execute_activity(
            activities.score_models, case, start_to_close_timeout=_SHORT, retry_policy=_RETRY
        )

        # An unavailable model forces the standard tier and a human route.
        model_health = "GREEN" if models.available else "RED"
        tier = "STANDARD" if not models.available else self._tier_for(case, policy)

        self._state = "COMMITTEE"
        committee: CommitteeOutcome = await workflow.execute_activity(
            activities.run_committee, args=[case, tier], start_to_close_timeout=_LONG, retry_policy=_RETRY
        )

        return await self._record_and_settle(case, policy, committee, model_health)

    # -- internals ----------------------------------------------------------
    @staticmethod
    def _tier_for(case: CaseRef, policy: PolicyOutcome) -> str:
        """Placeholder tier selection until T-044 implements docs/05 tier rules."""
        return "FAST" if float(case.requested_amount) <= 10000 else "STANDARD"

    async def _record_and_settle(
        self,
        case: CaseRef,
        policy: PolicyOutcome,
        committee: CommitteeOutcome,
        model_health: str,
    ) -> DecisionOutcome:
        self._state = "SYNTHESIZE"
        record = await workflow.execute_activity(
            activities.decide,
            args=[case, policy, committee, model_health],
            start_to_close_timeout=_SHORT,
            retry_policy=_RETRY,
        )

        self._state = "RECORDING"
        outcome: DecisionOutcome = await workflow.execute_activity(
            activities.record_decision,
            args=[case, record],
            start_to_close_timeout=_SHORT,
            retry_policy=_RETRY,
        )
        self._decision = outcome

        if outcome.route in _TERMINAL_ROUTES:
            self._state = "AUTONOMOUS"
            token = await workflow.execute_activity(
                activities.issue_token,
                args=[case, outcome.decision_record_id, None],
                start_to_close_timeout=_SHORT,
                retry_policy=_RETRY,
            )
            outcome.token_id = token["token_id"]
            self._state = "DONE"
            self._decision = outcome
            return outcome

        self._state = f"AWAITING_{outcome.route}"
        signal = await self._await_human(outcome.route)

        self._state = "HUMAN_DECISION"
        human = await workflow.execute_activity(
            activities.record_human_decision,
            args=[case, outcome.decision_record_id, asdict(signal)],
            start_to_close_timeout=_SHORT,
            retry_policy=_RETRY,
        )
        outcome.human_decision_id = human["human_decision_id"]
        outcome.final_action = human["final_action"]

        if human["final_action"] in ("APPROVE", "APPROVE_WITH_CONDITIONS"):
            token = await workflow.execute_activity(
                activities.issue_token,
                args=[case, outcome.decision_record_id, outcome.human_decision_id],
                start_to_close_timeout=_SHORT,
                retry_policy=_RETRY,
            )
            outcome.token_id = token["token_id"]

        self._state = "DONE"
        self._decision = outcome
        return outcome

    async def _await_human(self, route: str) -> HumanDecisionSignal:
        """Wait for a decision, escalating once the SLA passes but never deciding."""
        sla = _HUMAN_SLA.get(route, timedelta(hours=24))
        try:
            await workflow.wait_condition(lambda: self._human is not None, timeout=sla)
        except TimeoutError:
            # The SLA is a prompt to escalate, not permission to decide. The
            # workflow keeps waiting for a person (CLAUDE.md §2.7).
            self._state = f"ESCALATED_{route}"
            workflow.logger.info("SLA passed for %s; escalating and continuing to wait", route)
            await workflow.wait_condition(lambda: self._human is not None)

        assert self._human is not None
        return self._human
