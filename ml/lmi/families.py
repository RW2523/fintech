"""The six longitudinal feature families (docs/07 §4.2).

Payment timing lives in `temporal.py` because everything else is measured
against it. These are the families that corroborate or contradict it: a member
whose payments slipped and whose salary deduction also stopped is a different
case from one whose payments slipped while everything else held.

Corroboration is the point. One family moving is noise often enough that acting
on it teaches officers to ignore alerts; two families moving together is a
member whose circumstances changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from ml.lmi.temporal import Baseline, baseline_of, robust_z, slope

__all__ = [
    "FAMILIES",
    "Deduction",
    "Interaction",
    "SavingsPoint",
    "capacity_features",
    "deduction_features",
    "employer_gap",
    "interaction_features",
    "savings_features",
]

#: docs/07 §4.2 — every family, in the order a reader should think about them.
FAMILIES = (
    "payment_timing",
    "deduction_integrity",
    "savings_shares",
    "capacity",
    "interaction",
    "recovery",
)


@dataclass(frozen=True, slots=True)
class Deduction:
    """One payroll cycle's salary deduction."""

    cycle: date
    expected: float
    actual: float
    employer_id: str = ""

    @property
    def missed(self) -> bool:
        """Missed means nothing arrived, not that less arrived.

        A short deduction is a different fact from an absent one: the first is
        usually a pay change, the second is usually the employer's file.
        """
        return self.actual <= 0.0

    @property
    def shortfall(self) -> float:
        return round(max(self.expected - self.actual, 0.0), 2)


@dataclass(frozen=True, slots=True)
class SavingsPoint:
    balance: float
    at: date
    contribution: float = 0.0


@dataclass(frozen=True, slots=True)
class Interaction:
    """A contact with the member, and whether they answered."""

    at: date
    kind: str
    responded: bool = False
    promise_made: bool = False
    promise_kept: bool | None = None


def _within(items: list[Any], as_of: date, window: int, attribute: str) -> list[Any]:
    start = as_of - timedelta(days=window)
    return [item for item in items if start <= getattr(item, attribute) <= as_of]


def deduction_features(
    deductions: list[Deduction], *, as_of: date, employer_cycles: dict[date, float] | None = None
) -> dict[str, float]:
    """Whether the salary deduction is still arriving (docs/07 §4.2).

    The most valuable early signal in a payroll-deduction book, because it moves
    before the member does anything: they do not choose to stop, their employer
    does, and the member usually finds out when the platform does.
    """
    recent = _within(deductions, as_of, 90, "cycle")
    out: dict[str, float] = {
        "deduction_missed_count_90d": float(sum(1 for d in recent if d.missed)),
        "deduction_cycles_90d": float(len(recent)),
        "deduction_amount_delta_90d": round(sum(d.shortfall for d in recent), 2),
    }

    older = [d for d in deductions if d.cycle < as_of - timedelta(days=90)]
    if older and recent:
        before = sum(d.actual for d in older[-3:]) / min(len(older), 3)
        now = sum(d.actual for d in recent[-3:]) / min(len(recent), 3)
        out["deduction_level_change"] = round(now - before, 2)

    # An employer that missed a whole cycle is not a member who stopped paying,
    # and treating it as one raises an alert on everybody at that employer at
    # once. The threshold is the employer's own file, not this member's.
    if employer_cycles:
        out["employer_gap_flag"] = float(employer_gap(recent, employer_cycles))
    return out


#: docs/07 §4.2 — the share of an employer's members who must have missed the
#: same cycle before it is read as the employer's problem rather than the
#: member's.
EMPLOYER_GAP_SHARE = 0.30


def employer_gap(deductions: list[Deduction], employer_cycles: dict[date, float]) -> bool:
    """True when this member's missed cycle is one their employer missed too."""
    return any(
        deduction.missed and employer_cycles.get(deduction.cycle, 0.0) >= EMPLOYER_GAP_SHARE
        for deduction in deductions
    )


def savings_features(points: list[SavingsPoint], *, as_of: date) -> dict[str, float]:
    """Whether a member is still putting money aside (docs/07 §4.2).

    Saving stops before paying stops. A member under pressure protects the
    instalment and gives up the savings, so the balance flattens months before
    anything is late.
    """
    window = _within(points, as_of, 180, "at")
    if not window:
        return {"savings_slope_180d": 0.0, "savings_paused_months": 0.0}

    origin = window[0].at
    trend = slope([(float((p.at - origin).days), p.balance) for p in window])

    paused = 0
    for point in reversed(window):
        if point.contribution > 0:
            break
        paused += 1

    return {
        "savings_slope_180d": trend,
        "savings_paused_months": float(paused),
        "savings_balance": round(window[-1].balance, 2),
    }


def capacity_features(
    *,
    dsr_history: list[tuple[date, float]],
    as_of: date,
    new_obligations_6m: int = 0,
) -> dict[str, float]:
    """How much room the member has, and which way it is moving."""
    start = as_of - timedelta(days=180)
    window = [(at, value) for at, value in dsr_history if start <= at <= as_of]
    if not window:
        return {"dsr_trend_180d": 0.0, "new_obligations_6m": float(new_obligations_6m)}

    origin = window[0][0]
    return {
        "dsr_trend_180d": slope([(float((at - origin).days), value) for at, value in window]),
        "dsr_current": round(window[-1][1], 4),
        "new_obligations_6m": float(new_obligations_6m),
    }


def interaction_features(interactions: list[Interaction], *, as_of: date) -> dict[str, float]:
    """Whether the member is still talking to the cooperative.

    Someone who stops answering has usually decided something. It is a weak
    signal alone and a strong one beside a payment that slipped.
    """
    window = _within(interactions, as_of, 365, "at")
    contacted = [i for i in window if i.kind in ("CALL", "SMS", "EMAIL", "VISIT")]
    promises = [i for i in window if i.promise_made and i.promise_kept is not None]

    return {
        "contact_response_rate": round(sum(1 for i in contacted if i.responded) / len(contacted), 4)
        if contacted
        else 0.0,
        "contacts_12m": float(len(contacted)),
        "promise_kept_rate": round(sum(1 for i in promises if i.promise_kept) / len(promises), 4)
        if promises
        else 0.0,
        "extension_requests_12m": float(sum(1 for i in window if i.kind == "EXTENSION_REQUEST")),
    }


def baselines_for(series: dict[str, list[float]]) -> dict[str, Baseline]:
    """A personal baseline per signal, for whatever was measured."""
    return {signal: baseline_of(signal, values) for signal, values in series.items()}


def departures(features: dict[str, float], baselines: dict[str, Baseline]) -> dict[str, float]:
    """How far each feature sits from this member's own habit.

    Only signals with a baseline are reported. A feature nobody has a history
    for is a number, not a departure, and giving it a z of zero would let it
    read as "normal for them" when nothing is known at all.
    """
    return {
        f"{signal}_robust_z": robust_z(features[signal], baseline)
        for signal, baseline in baselines.items()
        if signal in features and baseline.usable
    }
