"""The early-warning case (docs/08 §8.2).

A member who has not applied for anything, whose behaviour changed, and whom
somebody may need to contact. Everything about this workflow follows from that
one fact: nobody asked the platform to look.

So it may propose a conversation and a verification, and it may never take an
action against the member. It closes itself when they come back. And it waits
for a person on anything above the lightest touch, because a member who has not
been told they are being watched has not agreed to be acted upon.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from workflows import activities
    from workflows.shared import CaseRef

__all__ = ["EarlyWarningCase", "EarlyWarningOutcome", "InterventionSignal"]

_SHORT = timedelta(seconds=30)
_COUNCIL = timedelta(minutes=20)
_RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=1))

#: How long the case waits before looking again. A drift does not change
#: hourly, and a workflow that re-ran nightly would produce a new record for a
#: member nobody has done anything about yet.
_REVIEW_EVERY = timedelta(days=7)

#: How long it waits for a person before giving up on the approval. Not on the
#: case: the case stays open and is reconsidered at the next review.
_APPROVAL_SLA = timedelta(days=3)


@dataclass
class InterventionSignal:
    """A person's answer on a proposed intervention."""

    actor_id: str
    role: str
    approved: list[str] = field(default_factory=list)
    declined: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class EarlyWarningOutcome:
    member_id: str
    alert_id: str
    state: str = "STABLE"
    decision_record_id: str = ""
    recommendation: str = "MONITOR"
    route: str = "OFFICER_REVIEW"
    proposed_actions: list[dict[str, Any]] = field(default_factory=list)
    approved_actions: list[str] = field(default_factory=list)
    reviews: int = 0
    closed: bool = False
    close_reason: str | None = None


@workflow.defn(name="EarlyWarningCase")
class EarlyWarningCase:
    """One member, watched until they come back or somebody acts."""

    def __init__(self) -> None:
        self._state = "CREATED"
        self._outcome: EarlyWarningOutcome | None = None
        self._intervention: InterventionSignal | None = None
        self._closed = False

    @workflow.signal
    def intervention_decided(self, signal: InterventionSignal) -> None:
        self._intervention = signal

    @workflow.signal
    def member_recovered(self) -> None:
        """The state machine says the member returned to STABLE.

        Closing on a signal rather than waiting for the next review, because a
        member who has recovered should not receive an outreach the platform
        queued a week ago.
        """
        self._closed = True

    @workflow.query
    def state(self) -> str:
        return self._state

    @workflow.query
    def outcome(self) -> dict[str, Any] | None:
        return asdict(self._outcome) if self._outcome else None

    @workflow.run
    async def run(self, case: CaseRef, alert_id: str, reviews: int = 0) -> EarlyWarningOutcome:
        outcome = EarlyWarningOutcome(member_id=case.member_id, alert_id=alert_id, reviews=reviews)

        self._state = "READING"
        watch = await workflow.execute_activity(
            activities.read_member_watch,
            args=[case],
            start_to_close_timeout=_SHORT,
            retry_policy=_RETRY,
        )
        outcome.state = str(watch.get("state") or "STABLE")

        # A member who recovered before the case was picked up needs no case.
        if outcome.state in ("STABLE", "RECOVERY") and reviews == 0:
            outcome.closed = True
            outcome.close_reason = f"the member was {outcome.state} when the case opened"
            self._state, self._outcome = "CLOSED", outcome
            return outcome

        self._state = "DELIBERATING"
        record = await workflow.execute_activity(
            activities.run_longitudinal_council,
            args=[case, alert_id, watch],
            start_to_close_timeout=_COUNCIL,
            retry_policy=_RETRY,
        )
        outcome.decision_record_id = str(record.get("decision_record_id") or "")
        outcome.recommendation = str(record.get("recommendation") or "MONITOR")
        outcome.route = str(record.get("route") or "OFFICER_REVIEW")
        outcome.proposed_actions = list(record.get("proposed_actions") or [])

        # docs/05 §6 — an early-warning case may never carry an L3 action. The
        # synthesizer drops them; this is the second check, because a workflow
        # that trusted the record would execute whatever reached it.
        outcome.proposed_actions = [
            action for action in outcome.proposed_actions if action.get("level") != "L3"
        ]

        if outcome.recommendation == "INTERVENE" and outcome.proposed_actions:
            self._state = "AWAITING_APPROVAL"
            approved = await self._await_intervention(case, outcome)
            outcome.approved_actions = approved

        # --- wait, then look again -----------------------------------------
        self._state = "WATCHING"
        self._outcome = outcome
        await workflow.wait_condition(lambda: self._closed, timeout=_REVIEW_EVERY)

        if self._closed:
            outcome.closed = True
            outcome.close_reason = "the member returned to STABLE"
            self._state = "CLOSED"
            self._outcome = outcome
            return outcome

        # Continue as new rather than looping: a case that runs for months
        # would carry every review's history in one workflow, and Temporal has
        # to keep all of it to replay the run.
        workflow.continue_as_new(args=[case, alert_id, reviews + 1])

    async def _await_intervention(self, case: CaseRef, outcome: EarlyWarningOutcome) -> list[str]:
        """Wait for a person on anything the platform proposes.

        L1 actions could auto-execute under a permissive dial. They do not
        here: an early-warning case is the platform acting on somebody who has
        not been told it is watching, and the first contact about that is a
        person's to make.
        """
        try:
            await workflow.wait_condition(lambda: self._intervention is not None, timeout=_APPROVAL_SLA)
        except TimeoutError:
            # The SLA is a prompt, not permission. The case stays open and is
            # reconsidered at the next review.
            workflow.logger.info("no answer on %s within the SLA; deferring", outcome.alert_id)
            return []

        assert self._intervention is not None
        signal = self._intervention
        if not signal.approved:
            return []

        executed = await workflow.execute_activity(
            activities.carry_out_intervention,
            args=[case, outcome.decision_record_id, signal.approved, asdict(signal)],
            start_to_close_timeout=_SHORT,
            retry_policy=_RETRY,
        )
        return list(executed.get("executed") or [])
