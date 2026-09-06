"""Temporal features and personal baselines (docs/07 §4.2).

The credit-risk model asks whether this member looks like the members who
defaulted. This asks a different question: whether this member looks like
themselves. A member who has always paid three days late has not changed when
they pay three days late, and one who has always paid on the day has, when they
pay two days late twice.

That is why every feature here is paired with a personal baseline. The absolute
value is rarely the signal; the departure from a member's own habit is.

Pure functions over series. Nothing here reads a database, so a behaviour can
be constructed in a test and the arithmetic checked against it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

__all__ = [
    "EPSILON",
    "MAD_SCALE",
    "MAX_Z",
    "WINDOWS",
    "Baseline",
    "DueEvent",
    "baseline_of",
    "days_to_pay",
    "late_streak",
    "recovery_features",
    "robust_z",
    "slope",
    "window_features",
]

#: docs/07 §4.2 — the windows every family is measured over, in days.
WINDOWS = (7, 30, 90, 180, 365)

#: Makes the median absolute deviation comparable to a standard deviation for
#: normally distributed data, so a robust z reads on the scale people expect.
MAD_SCALE = 1.4826

#: Keeps a member whose behaviour never varies from dividing by zero. A member
#: who has paid on exactly the same day for a year has a MAD of zero, and the
#: first day they are late would otherwise be infinitely surprising.
EPSILON = 1e-9

#: Where the number stops carrying meaning. With a MAD near zero the epsilon
#: above turns a small departure into billions, which is not a measurement of
#: anything: it says the member has no variation, which the magnitude cannot
#: express. Ten robust deviations already means "unlike anything this member
#: has done", and every consumer treats further as the same thing.
MAX_Z = 10.0


@dataclass(frozen=True, slots=True)
class DueEvent:
    """One instalment falling due, and what happened to it."""

    due_date: date
    amount_due: float
    paid_at: date | None = None
    amount_paid: float = 0.0

    @property
    def days_to_pay(self) -> int | None:
        """Negative is early. None means it has not been paid at all.

        Unpaid is not "very late": a due event with no payment against it is a
        different fact from one paid sixty days on, and averaging the two
        together would let a member with one unpaid instalment look like a
        member who is merely slow.
        """
        if self.paid_at is None:
            return None
        return (self.paid_at - self.due_date).days

    @property
    def shortfall(self) -> float:
        return round(max(self.amount_due - self.amount_paid, 0.0), 2)


def days_to_pay(events: list[DueEvent]) -> list[int]:
    """Every settled due event's timing, oldest first."""
    return [event.days_to_pay for event in events if event.days_to_pay is not None]


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return float(ordered[middle - 1] + ordered[middle]) / 2.0


def _percentile(values: list[float], q: float) -> float:
    """Linear interpolation between order statistics, as numpy does it."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = q * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[int(position)])
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


@dataclass(frozen=True, slots=True)
class Baseline:
    """What normal looks like for one member on one signal."""

    signal: str
    median: float
    mad: float
    n: int

    @property
    def usable(self) -> bool:
        """Whether there is enough history to call anything unusual.

        Fewer than six observations and the median is an opinion. Reporting a
        robust z off three payments would flag every new member as changed on
        their fourth.
        """
        return self.n >= 6

    def z(self, value: float) -> float:
        return robust_z(value, self)

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal,
            "median_365d": round(self.median, 4),
            "mad_365d": round(self.mad, 4),
            "n": self.n,
            "usable": self.usable,
        }


def baseline_of(signal: str, values: list[float]) -> Baseline:
    """A member's own median and dispersion on one signal."""
    median = _median(values)
    mad = _median([abs(value - median) for value in values])
    return Baseline(signal=signal, median=median, mad=mad, n=len(values))


def robust_z(value: float, baseline: Baseline) -> float:
    """How far from this member's own habit, in robust standard deviations.

    Returns 0.0 when the baseline is not usable rather than a large number: an
    unknown habit is not evidence of a departure from it. Clamped to ±MAX_Z,
    because a member with no variation divides by the epsilon and produces a
    number with no meaning attached to its size.
    """
    if not baseline.usable:
        return 0.0
    z = (value - baseline.median) / (MAD_SCALE * baseline.mad + EPSILON)
    return round(max(-MAX_Z, min(MAX_Z, z)), 4)


def slope(points: list[tuple[float, float]]) -> float:
    """Ordinary least squares slope, per unit of x.

    Written out rather than pulled from a library because it runs on a handful
    of points inside a per-member loop, and because a slope over one point is
    zero rather than an error.
    """
    if len(points) < 2:
        return 0.0
    n = float(len(points))
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    variance = sum((x - mean_x) ** 2 for x, _ in points)
    if variance <= 0:
        return 0.0
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in points)
    return round(covariance / variance, 6)


def late_streak(events: list[DueEvent], *, tolerance_days: int = 0) -> int:
    """Consecutive late due events ending at the most recent one.

    Counted backwards from now, so a member who was late four times last year
    and has paid on time since has a streak of zero. The streak is about what
    is happening, not about what happened.
    """
    streak = 0
    for event in reversed(events):
        timing = event.days_to_pay
        if timing is None or timing > tolerance_days:
            streak += 1
            continue
        break
    return streak


def _within(events: list[DueEvent], as_of: date, window: int) -> list[DueEvent]:
    start = as_of - timedelta(days=window)
    return [event for event in events if start <= event.due_date <= as_of]


def window_features(events: list[DueEvent], *, as_of: date) -> dict[str, float]:
    """Payment timing over every documented window (docs/07 §4.2).

    A window with no due event in it reports nothing rather than zero: a member
    with no instalment due in the last seven days has not paid on time this
    week, and recording that as perfect punctuality is how a quiet month looks
    like an improvement.
    """
    out: dict[str, float] = {}
    for window in WINDOWS:
        inside = _within(events, as_of, window)
        timings = days_to_pay(inside)
        if not timings:
            continue
        out[f"days_to_pay_median_{window}d"] = round(_median([float(t) for t in timings]), 3)
        out[f"days_late_p95_{window}d"] = round(_percentile([float(max(t, 0)) for t in timings], 0.95), 3)
        out[f"due_events_{window}d"] = float(len(inside))
        unpaid = sum(1 for event in inside if event.days_to_pay is None)
        out[f"unpaid_{window}d"] = float(unpaid)

    out["late_streak"] = float(late_streak(events))

    # The trend is over due events rather than over calendar days, because a
    # member whose instalments are monthly has twelve points a year and a
    # regression on the date axis would be dominated by the gaps.
    settled = [event for event in _within(events, as_of, 180) if event.days_to_pay is not None]
    out["due_to_pay_slope_180d"] = slope(
        [(float(index), float(event.days_to_pay or 0)) for index, event in enumerate(settled)]
    )
    return out


@dataclass
class RecoveryState:
    """What has happened since somebody raised an alert on this member."""

    alert_at: date | None = None
    on_time_since: int = 0
    events_since: int = 0
    distance_to_baseline: float = 0.0
    extras: dict[str, float] = field(default_factory=dict)


#: docs/07 §4.2 — how fast a raised concern decays as a member pays on time.
#: Six consecutive on-time payments take the weight to about 0.4, which is the
#: point at which the state machine will consider stepping back down.
RISK_DECAY_RATE = 0.15


def recovery_features(
    events: list[DueEvent], *, alert_at: date | None, baseline: Baseline
) -> dict[str, float]:
    """How far a member has come back since a concern was raised.

    A platform that raises concerns and never withdraws them teaches people to
    ignore it. Recovery is measured the same way the concern was: against the
    member's own baseline.
    """
    if alert_at is None:
        return {"consecutive_on_time_since_alert": 0.0, "risk_decay": 1.0}

    since = [event for event in events if event.due_date >= alert_at]
    on_time = 0
    for event in since:
        timing = event.days_to_pay
        if timing is not None and timing <= 0:
            on_time += 1
        else:
            on_time = 0

    latest = [e.days_to_pay for e in since if e.days_to_pay is not None]
    distance = abs(robust_z(float(latest[-1]), baseline)) if latest else 0.0

    return {
        "consecutive_on_time_since_alert": float(on_time),
        "due_events_since_alert": float(len(since)),
        "distance_to_baseline": round(distance, 4),
        # Falls towards zero as a member proves the concern was answered.
        "risk_decay": round(math.exp(-RISK_DECAY_RATE * on_time), 4),
    }
