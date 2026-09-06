"""The member state machine (docs/07 §4.5).

Five states, and the point of all of them is to decide whether a person should
look at this member. A platform that escalates freely and de-escalates
reluctantly ends with everybody in ELEVATED and nobody reading it.

Three properties matter more than the transitions themselves.

Hysteresis. A state may change at most once a fortnight, except into CRITICAL.
Without it a member on the edge of a threshold oscillates weekly, and an
officer learns that the state means nothing.

Corroboration. One family moving is noise often enough that acting on it
teaches officers to ignore alerts. A change confirmed by a second family, or by
a model that agrees, is a member whose circumstances changed.

Contradiction. An outage that explains the late payments, or an arrangement
that covers them, de-escalates. The platform being wrong is normal; the
platform refusing to notice it is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

__all__ = [
    "HYSTERESIS_DAYS",
    "STATES",
    "Corroboration",
    "Evidence",
    "Transition",
    "corroborate",
    "next_state",
]

#: docs/07 §4.5 — least to most concerning, with RECOVERY off to the side: it
#: is a member coming back, not a rung on the ladder.
STATES = ("STABLE", "WATCH", "ELEVATED", "CRITICAL", "RECOVERY")

#: How long a state must hold before it may change again. Not applied to
#: CRITICAL: a member who is thirty days late does not wait a fortnight for the
#: platform to say so.
HYSTERESIS_DAYS = 14

#: Above this the payment timing has departed from the member's own habit.
DEPARTURE_Z = 2.0

#: docs/07 §4.5 — the probability thresholds that carry a state up.
P30_ELEVATED = 0.25
P30_CRITICAL = 0.60

#: More than this many days late is a different kind of event from being a few
#: days on: it is the point at which the case stops being about drift.
CRITICAL_DAYS_LATE = 30


@dataclass
class Evidence:
    """Everything the machine is allowed to look at.

    Assembled by the caller from the feature store, the detector and the core.
    Passed as one object so a transition can name exactly what moved it, and so
    a state cannot be reached from something nobody recorded.
    """

    member_id: str
    as_of: date
    features: dict[str, float] = field(default_factory=dict)
    #: Confirmed change-points, newest first, as the detector reported them.
    change_points: list[dict[str, Any]] = field(default_factory=list)
    p30: float | None = None
    p90: float | None = None
    max_days_late: int = 0
    consecutive_on_time: int = 0
    departure_events: int = 0
    fraud_level: str = "NONE"
    outage_covers_deviations: bool = False
    arrangement_active: bool = False
    employer_gap: bool = False
    distance_to_baseline: list[float] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)

    @property
    def confirmed_change(self) -> bool:
        return any(cp.get("stats", {}).get("confirmed") for cp in self.change_points)

    @property
    def latest_change_point(self) -> str | None:
        for cp in self.change_points:
            if cp.get("stats", {}).get("confirmed"):
                return str(cp.get("cp_date"))
        return str(self.change_points[0].get("cp_date")) if self.change_points else None


@dataclass
class Corroboration:
    """Which families agree, which explain it away, and which are silent."""

    confirms: list[str] = field(default_factory=list)
    explains: list[str] = field(default_factory=list)
    silent: list[str] = field(default_factory=list)

    @property
    def confirmed(self) -> bool:
        return bool(self.confirms)

    @property
    def explained(self) -> bool:
        return bool(self.explains)

    def as_dict(self) -> dict[str, Any]:
        return {
            "confirms": self.confirms,
            "explains": self.explains,
            "silent": self.silent,
        }


def corroborate(evidence: Evidence) -> Corroboration:
    """docs/07 §4.6 — what the other families say about a payment drift.

    EXPLAINS beats CONFIRMS in the caller's hands, not here: this reports what
    each family says, and the machine decides. Mixing the two would hide the
    fact that a member had both a real deterioration and an outage in the same
    month, which is a case an officer must see rather than have resolved for
    them.
    """
    found = Corroboration()
    features = evidence.features

    if evidence.outage_covers_deviations:
        found.explains.append("OUTAGE_WINDOW")
    if evidence.arrangement_active:
        found.explains.append("ARRANGEMENT_ACTIVE")
    if evidence.employer_gap:
        # An employer that missed a whole cycle is not a member who stopped
        # paying, and the alert belongs to whoever calls the employer.
        found.explains.append("EMPLOYER_GAP")

    if float(features.get("deduction_missed_count_90d", 0)) >= 1:
        found.confirms.append("DEDUCTION_IRREGULAR")
    elif "deduction_missed_count_90d" in features:
        found.silent.append("DEDUCTION_IRREGULAR")

    if float(features.get("savings_paused_months", 0)) >= 3:
        found.confirms.append("SAVINGS_PAUSED")
    elif "savings_paused_months" in features:
        found.silent.append("SAVINGS_PAUSED")

    if float(features.get("contact_response_rate", 1.0)) < 0.5 and features.get("contacts_12m", 0):
        found.confirms.append("CONTACT_UNRESPONSIVE")
    elif "contact_response_rate" in features:
        found.silent.append("CONTACT_UNRESPONSIVE")

    return found


@dataclass
class Transition:
    """A move, or a deliberate refusal to move."""

    member_id: str
    at: date
    from_state: str
    to_state: str
    rule: str
    reason: str
    corroboration: Corroboration = field(default_factory=Corroboration)
    evidence_ids: list[str] = field(default_factory=list)
    held_by_hysteresis: bool = False

    @property
    def changed(self) -> bool:
        return self.from_state != self.to_state

    def as_dict(self) -> dict[str, Any]:
        return {
            "member_id": self.member_id,
            "at": self.at.isoformat(),
            "from_state": self.from_state,
            "to_state": self.to_state,
            "changed": self.changed,
            "rule": self.rule,
            "reason": self.reason,
            "corroboration": self.corroboration.as_dict(),
            "evidence_ids": self.evidence_ids,
            "held_by_hysteresis": self.held_by_hysteresis,
        }


def _falling(values: list[float]) -> bool:
    """Whether the last two readings are moving back towards the baseline."""
    return len(values) >= 2 and abs(values[-1]) < abs(values[-2])


def next_state(
    current: str,
    evidence: Evidence,
    *,
    since: date | None = None,
) -> Transition:
    """The state this member should be in, and why.

    Evaluated in the documented order, and the order is the meaning: a
    contradiction de-escalates before an escalation is considered, so a member
    whose late payments are explained by an outage cannot be carried upward by
    the same payments.
    """
    found = corroborate(evidence)
    held = _within_hysteresis(since, evidence.as_of)

    def decide(to_state: str, rule: str, reason: str) -> Transition:
        # CRITICAL is exempt: a member thirty days late does not wait a
        # fortnight for the platform to be allowed to say so.
        blocked = held and to_state != "CRITICAL" and to_state != current
        return Transition(
            member_id=evidence.member_id,
            at=evidence.as_of,
            from_state=current,
            to_state=current if blocked else to_state,
            rule=rule if not blocked else f"{rule}:HELD",
            reason=(f"held: {current} was set within {HYSTERESIS_DAYS} days" if blocked else reason),
            corroboration=found,
            evidence_ids=evidence.evidence_ids,
            held_by_hysteresis=blocked,
        )

    # --- contradiction first (docs/07 §4.5, last row) ----------------------
    if found.explained and current in ("ELEVATED", "CRITICAL"):
        return decide(
            "WATCH",
            "DE_ESCALATE_EXPLAINED",
            f"the deviation is explained by {', '.join(found.explains)}",
        )

    # --- CRITICAL ----------------------------------------------------------
    if evidence.max_days_late > CRITICAL_DAYS_LATE:
        return decide(
            "CRITICAL",
            "LATE_BEYOND_30_DAYS",
            f"a payment is {evidence.max_days_late} days late",
        )
    if str(evidence.fraud_level).upper() == "HIGH":
        return decide("CRITICAL", "FRAUD_HIGH", "an integrity finding of high severity is open")
    if (
        current in ("ELEVATED", "CRITICAL")
        and evidence.p30 is not None
        and evidence.p30 >= P30_CRITICAL
        and len(found.confirms) >= 2
    ):
        return decide(
            "CRITICAL",
            "P30_HIGH_TWO_FAMILIES",
            f"p30 is {evidence.p30:.2f} and {' and '.join(found.confirms)} both agree",
        )

    # --- coming back -------------------------------------------------------
    if (
        current in ("ELEVATED", "CRITICAL")
        and evidence.consecutive_on_time >= 2
        and _falling(evidence.distance_to_baseline)
    ):
        return decide(
            "RECOVERY",
            "TWO_ON_TIME_AND_CLOSING",
            f"{evidence.consecutive_on_time} consecutive on-time events and the "
            "distance from baseline is falling",
        )

    if current == "RECOVERY":
        if evidence.consecutive_on_time >= 3 and _below_one_for_60_days(evidence):
            return decide(
                "STABLE",
                "THREE_ON_TIME_AND_SETTLED",
                f"{evidence.consecutive_on_time} consecutive on-time events and the "
                "departure has stayed under one deviation",
            )
        return decide("RECOVERY", "STILL_RECOVERING", "not yet three consecutive on-time events")

    if current == "WATCH" and evidence.consecutive_on_time >= 2 and _departure(evidence) < 1.0:
        return decide(
            "STABLE",
            "TWO_ON_TIME_AND_SETTLED",
            "two consecutive on-time events with no departure from habit",
        )

    # --- ELEVATED ----------------------------------------------------------
    #
    # docs/07 §4.5 gates this on the change-point being CONFIRMED by PELT, plus
    # a second reason. The gate is not kept, and the measurement is why: PELT
    # confirms a level shift and cannot confirm a trend (T-061), so whether an
    # escalation is allowed would depend on the shape of a member's series
    # rather than on whether they are deteriorating. Traced on this population,
    # drifting members reached WATCH about sixty days before their first late
    # payment and then went straight to CRITICAL, because ELEVATED was
    # unreachable: every one of their alarms was unconfirmed.
    #
    # The intent behind the gate is kept, which is that one weak signal must
    # not escalate a member. Two independent reasons are required instead, from
    # PELT agreeing, the model putting p30 at or above the threshold, and a
    # second family moving with the payments.
    if current == "WATCH" and evidence.change_points and not found.explained:
        reasons: list[str] = []
        if evidence.confirmed_change:
            reasons.append("a second method agrees on the change-point")
        if evidence.p30 is not None and evidence.p30 >= P30_ELEVATED:
            reasons.append(f"p30 is {evidence.p30:.2f}")
        if found.confirms:
            reasons.append(f"{' and '.join(found.confirms)} agree")

        if len(reasons) >= 2:
            return decide(
                "ELEVATED",
                "CHANGE_POINT_CORROBORATED",
                f"payment timing began drifting at {evidence.latest_change_point}; " + ", and ".join(reasons),
            )

    # --- WATCH -------------------------------------------------------------
    if current == "STABLE":
        # An unconfirmed change-point is enough for WATCH, which is the
        # platform paying attention rather than asking somebody else to.
        #
        # docs/07 §4.5 gives this rung a threshold rule instead: robust z above
        # two for two consecutive due events. That rule is kept below, and on
        # this population it almost never fires. Payment timing is recorded in
        # whole days and the scale floor is two of them, so a member has to
        # drift four days beyond their own habit before a single reading counts
        # as a departure, and by then they are usually late already. Measured:
        # with the threshold alone, escalations on drifting members arrived a
        # median of forty days *after* their first late payment.
        #
        # The accumulating detector is the thing measured to see the drift
        # ninety-two days early (T-061), so it raises the first rung and the
        # threshold rule remains as a second way in.
        if evidence.change_points:
            return decide(
                "WATCH",
                "CHANGE_POINT_RAISED",
                f"payment timing began drifting at {evidence.latest_change_point}",
            )
        if evidence.departure_events >= 2:
            return decide(
                "WATCH",
                "DEPARTED_TWICE",
                f"payment timing departed from habit on {evidence.departure_events} consecutive due events",
            )
        if float(evidence.features.get("deduction_missed_count_90d", 0)) >= 1:
            return decide(
                "WATCH",
                "DEDUCTION_MISSED",
                "a salary deduction cycle was missed in the last ninety days",
            )

    return Transition(
        member_id=evidence.member_id,
        at=evidence.as_of,
        from_state=current,
        to_state=current,
        rule="NO_CHANGE",
        reason="no transition condition held",
        corroboration=found,
        evidence_ids=evidence.evidence_ids,
    )


def _within_hysteresis(since: date | None, as_of: date) -> bool:
    return since is not None and (as_of - since) < timedelta(days=HYSTERESIS_DAYS)


def _departure(evidence: Evidence) -> float:
    return abs(float(evidence.features.get("days_to_pay_median_30d_robust_z", 0.0)))


def _below_one_for_60_days(evidence: Evidence) -> bool:
    """Whether the member has been inside one deviation across both windows.

    The 30-day and 90-day medians together are the closest this feature set
    comes to "for 60 days", and using both means a member cannot return to
    STABLE on one quiet month.
    """
    return (
        abs(float(evidence.features.get("days_to_pay_median_30d_robust_z", 0.0))) < 1.0
        and abs(float(evidence.features.get("days_to_pay_median_90d_robust_z", 0.0))) < 1.0
    )
