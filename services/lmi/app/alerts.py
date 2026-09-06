"""Turning state changes into a day's work (docs/07 §4.7).

An early-warning engine that raises everything it notices is an engine nobody
reads. This is the layer that decides what an officer actually sees, and every
rule in it exists to protect their attention.

One open alert per account, deduplicated on the set of signals that raised it,
ranked by what acting on it is worth, capped at what one person can do in a
day, and closed automatically when the member comes back.

The `why_now` line is not decoration. An alert an officer cannot act on without
first reconstructing the case is an alert they will leave until tomorrow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from cio_common.ids import derived_id

__all__ = [
    "DEFAULT_CAP_PER_OFFICER",
    "RESPONSIVE_UPLIFT",
    "Alert",
    "raise_alert",
    "rank_value",
    "shortlist",
    "why_now",
]

#: docs/07 §4.7 — how many a person can work through in a day. From policy in
#: production; this is what applies when policy says nothing.
DEFAULT_CAP_PER_OFFICER = 25

#: A member who answers the telephone is one an intervention can reach, so an
#: alert about them is worth more than the same alert about somebody who does
#: not. Not because they are more at risk: because acting on it can work.
RESPONSIVE_UPLIFT = 0.2
RESPONSIVE_THRESHOLD = 0.5


@dataclass
class Alert:
    """One thing an officer should look at, and why now."""

    alert_id: str
    member_id: str
    account_id: str | None
    state: str
    signals: tuple[str, ...]
    rank_value: float
    why_now: str
    p90: float | None = None
    exposure: float = 0.0
    change_point: str | None = None
    corroboration: dict[str, Any] = field(default_factory=dict)
    opened_at: date | None = None
    closed_at: date | None = None
    close_reason: str | None = None

    @property
    def open(self) -> bool:
        return self.closed_at is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "member_id": self.member_id,
            "account_id": self.account_id,
            "state": self.state,
            "signals": list(self.signals),
            "rank_value": round(self.rank_value, 4),
            "why_now": self.why_now,
            "p90": self.p90,
            "exposure": round(self.exposure, 2),
            "change_point": self.change_point,
            "corroboration": self.corroboration,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "close_reason": self.close_reason,
        }


def rank_value(*, p90: float | None, exposure: float, contact_response_rate: float | None) -> float:
    """docs/07 §4.7 — what acting on this alert is worth.

    Probability times exposure is the expected amount at risk. The uplift is
    the part people forget: an alert about somebody who never answers is worth
    less than the same alert about somebody who does, because the intervention
    can only work on the second.

    A member with no probability yet ranks at zero rather than being dropped:
    they still appear, at the bottom, where somebody may still look.
    """
    if p90 is None:
        return 0.0
    uplift = (
        RESPONSIVE_UPLIFT
        if contact_response_rate is not None and contact_response_rate > RESPONSIVE_THRESHOLD
        else 0.0
    )
    return float(p90) * float(exposure) * (1.0 + uplift)


def why_now(
    *,
    signal: str,
    baseline: float,
    current: float,
    days: int,
    change_point: str | None,
    corroboration: dict[str, Any],
) -> str:
    """docs/07 §4.7 — the sentence an officer reads first.

    Names the signal, what it was, what it is, over how long, and what agrees
    or explains it. Everything an officer needs to decide whether to open the
    case, in one line.
    """
    parts = [
        f"{signal} moved from {baseline:g} to {current:g} over {days} days",
    ]
    if change_point:
        parts.append(f"change-point {change_point}")

    confirms = corroboration.get("confirms") or []
    explains = corroboration.get("explains") or []
    if confirms:
        parts.append(f"{' and '.join(confirms).lower().replace('_', ' ')} agree")
    if explains:
        # Said even though an explained deviation does not escalate: an officer
        # reading a WATCH alert needs to know somebody already has a reason.
        parts.append(f"but {' and '.join(explains).lower().replace('_', ' ')} may explain it")
    if not confirms and not explains:
        parts.append("no other signal moved with it")
    return "; ".join(parts)


#: Which states raise an alert at all. WATCH does not: it is the platform
#: paying attention, not asking somebody else to.
ALERTING_STATES = ("ELEVATED", "CRITICAL")


def signal_set(transition: dict[str, Any], features: dict[str, float]) -> tuple[str, ...]:
    """What raised this alert, as a stable set.

    The dedupe key. Two alerts about the same member for the same reasons are
    one alert; the same member for a new reason is a new one, because the new
    reason is what an officer has not seen.
    """
    signals = {str(transition.get("rule", ""))}
    signals.update(transition.get("corroboration", {}).get("confirms") or [])
    if float(features.get("deduction_missed_count_90d", 0)) >= 1:
        signals.add("DEDUCTION_MISSED")
    if float(features.get("savings_paused_months", 0)) >= 3:
        signals.add("SAVINGS_PAUSED")
    return tuple(sorted(signal for signal in signals if signal))


def raise_alert(
    transition: dict[str, Any],
    *,
    features: dict[str, float],
    exposure: float,
    p90: float | None,
    account_id: str | None = None,
    change_point: str | None = None,
    opened_at: date | None = None,
) -> Alert | None:
    """One alert from one transition, or None when nothing should be raised."""
    state = str(transition.get("to_state", ""))
    if state not in ALERTING_STATES or not transition.get("changed"):
        return None

    signals = signal_set(transition, features)
    member_id = str(transition["member_id"])
    # Derived from the member, the account and the signal set, so the same
    # concern raised twice is the same alert rather than a second one.
    alert_id = derived_id("alert", member_id, account_id or "", *signals)

    baseline = float(features.get("baseline_median", 0.0))
    current = float(features.get("days_to_pay_median_30d", baseline))

    return Alert(
        alert_id=alert_id,
        member_id=member_id,
        account_id=account_id,
        state=state,
        signals=signals,
        rank_value=rank_value(
            p90=p90,
            exposure=exposure,
            contact_response_rate=features.get("contact_response_rate"),
        ),
        why_now=why_now(
            signal="days to pay",
            baseline=baseline,
            current=current,
            days=30,
            change_point=change_point,
            corroboration=transition.get("corroboration") or {},
        ),
        p90=p90,
        exposure=exposure,
        change_point=change_point,
        corroboration=dict(transition.get("corroboration") or {}),
        opened_at=opened_at or date.today(),
    )


def shortlist(alerts: list[Alert], *, cap: int = DEFAULT_CAP_PER_OFFICER, officers: int = 1) -> list[Alert]:
    """What actually reaches a queue today, highest value first.

    The cap is per officer and the queue is shared, so the list is the cap
    times the officers on duty. Everything below the line stays open and is
    considered again tomorrow: a capped alert is deferred, never dropped.
    """
    ranked = sorted(
        (alert for alert in alerts if alert.open),
        key=lambda alert: (-alert.rank_value, alert.member_id),
    )
    return ranked[: max(cap, 0) * max(officers, 1)]


def close_on_recovery(alerts: list[Alert], transition: dict[str, Any], *, at: date) -> list[Alert]:
    """docs/07 §4.7 — auto-close when a member comes back.

    A platform that raises concerns and never withdraws them teaches people to
    ignore it. Closing is as much a part of the alert's life as raising it.
    """
    if transition.get("to_state") != "STABLE" or transition.get("from_state") != "RECOVERY":
        return []

    member_id = str(transition["member_id"])
    closed: list[Alert] = []
    for alert in alerts:
        if alert.member_id == member_id and alert.open:
            alert.closed_at = at
            alert.close_reason = "the member returned to STABLE"
            closed.append(alert)
    return closed
