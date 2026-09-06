"""Payment behaviour by archetype (docs/10 §4).

`days_to_pay` is the signed distance from the due date: negative is early. The
archetypes are what make the Longitudinal Member Intelligence engine worth
having, so their shapes matter more than any single number.

The miss rates below are calibrated so the population lands inside the sanity
ranges in docs/10 §4.6; `synthetic/tests/test_sanity.py` is the check.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = [
    "LATE_THRESHOLD_DAYS",
    "MISSED_THRESHOLD_DAYS",
    "PaymentSeries",
    "days_to_pay_series",
]

#: A payment more than this many days late counts as a late event (docs/10 §5).
LATE_THRESHOLD_DAYS = 7
#: Beyond this the cycle is treated as missed rather than late.
MISSED_THRESHOLD_DAYS = 30

#: Earliest a payment may land relative to its due date.
_EARLIEST = -10

# --- SLOW_DRIFT ------------------------------------------------------------
#: The drift starts here, in absolute month index (docs/10 §4).
_DRIFT_FROM = 14
#: Chosen so the median first late event lands in month 19 or 20, with at least
#: 80 % of the cohort seeing one by month 21 (docs/10 §4.6).
_DRIFT_PER_MONTH = 1.6
_DRIFT_SIGMA_PER_MONTH = 0.2
#: Deduction receipts turn irregular from here, savings pause a month later.
DRIFT_DEDUCTION_FROM = 16
DRIFT_DEDUCTION_MISS_RATE = 0.30
DRIFT_SAVINGS_PAUSE_FROM = 17
#: Missed cycles only begin after the first late event is expected, so the
#: drift itself decides when the member first goes late, not a coin flip.
_DRIFT_MISS_FROM = 18
_DRIFT_MISS_PER_MONTH = 0.15
_DRIFT_MISS_CAP = 0.65

# --- SHOCK -----------------------------------------------------------------
_SHOCK_WINDOW = (8, 18)
_SHOCK_DELAY_DAYS = 12
_SHOCK_CYCLES = 3
_SHOCK_MISS_RATE = 0.70
_SHOCK_UNRECOVERED_MISS_RATE = 0.50
#: A member who is contacted recovers; one who is not stays late.
SHOCK_OUTREACH_RATE = 0.70

#: Baseline chance a cycle is missed outright, before archetype dynamics.
#: Calibrated so the portfolio lands in the 6-8 % band of docs/10 §4.6 with
#: STEADY under 1 % and CHRONIC over 25 %.
_BASE_MISS_RATE = {
    "STEADY": 0.006,
    "SEASONAL": 0.016,
    "IMPROVING": 0.045,
    # A drifting member is genuinely clean for fourteen months, so the baseline
    # is near zero: the drift decides when they first go late, not a coin flip.
    "SLOW_DRIFT": 0.002,
    "SHOCK": 0.025,
    "CHRONIC": 0.440,
}


@dataclass(frozen=True, slots=True)
class PaymentSeries:
    """One account's payment timing across the history window."""

    days_to_pay: tuple[float, ...]
    missed: tuple[bool, ...]
    shock_month: int | None = None
    recovers: bool = True

    def first_late_month(self, first_month: int = 1) -> int | None:
        """Absolute month of the first payment more than a week late."""
        for offset, days in enumerate(self.days_to_pay):
            if days > LATE_THRESHOLD_DAYS:
                return first_month + offset
        return None


def _miss_rate(archetype: str, month: int, shock_month: int | None, recovers: bool) -> float:
    """Chance this cycle is missed outright."""
    base = _BASE_MISS_RATE[archetype]

    if archetype == "SLOW_DRIFT" and month > _DRIFT_MISS_FROM:
        # deterioration compounds rather than appearing all at once
        return min(_DRIFT_MISS_CAP, base + _DRIFT_MISS_PER_MONTH * (month - _DRIFT_MISS_FROM))

    if archetype == "SHOCK" and shock_month is not None:
        if shock_month <= month < shock_month + _SHOCK_CYCLES:
            return _SHOCK_MISS_RATE
        if not recovers and month >= shock_month + _SHOCK_CYCLES:
            return _SHOCK_UNRECOVERED_MISS_RATE

    return base


def _mean_and_sigma(
    archetype: str, month: int, shock_month: int | None, recovers: bool
) -> tuple[float, float]:
    """The timing distribution for this archetype in this month."""
    if archetype == "STEADY":
        return -1.0, 1.5

    if archetype == "SEASONAL":
        # festive months, in both years of the window
        festive = month in (11, 12, 23, 24)
        return (4.0 if festive else 0.0), 2.0

    if archetype == "IMPROVING":
        return max(-1.0, 6.0 - 0.4 * (month - 1)), 3.0

    if archetype == "SLOW_DRIFT":
        if month <= _DRIFT_FROM:
            return -1.0, 1.5
        elapsed = month - _DRIFT_FROM
        return (-1.0 + _DRIFT_PER_MONTH * elapsed, 1.5 + _DRIFT_SIGMA_PER_MONTH * elapsed)

    if archetype == "SHOCK":
        if shock_month is None or month < shock_month:
            return -1.0, 1.5
        elapsed = month - shock_month
        if elapsed < _SHOCK_CYCLES:
            return float(_SHOCK_DELAY_DAYS), 4.0
        return (-1.0, 1.5) if recovers else (9.0, 5.0)

    # CHRONIC
    return 9.0, 6.0


def days_to_pay_series(
    archetype: str,
    months: list[int],
    rng: np.random.Generator,
) -> PaymentSeries:
    """Generate one account's timing across ``months`` (absolute month indices)."""
    shock_month: int | None = None
    recovers = True
    if archetype == "SHOCK":
        shock_month = int(rng.integers(_SHOCK_WINDOW[0], _SHOCK_WINDOW[1] + 1))
        recovers = bool(rng.random() < SHOCK_OUTREACH_RATE)

    # docs/10 §4 — a steady payer has a one-in-a-hundred chance of a single blip
    blip_month: int | None = None
    if archetype == "STEADY" and months and rng.random() < 0.01:
        blip_month = int(rng.choice(months))

    timings: list[float] = []
    missed: list[bool] = []

    for month in months:
        if rng.random() < _miss_rate(archetype, month, shock_month, recovers):
            # a missed cycle is a large positive gap, not a separate concept
            days = float(MISSED_THRESHOLD_DAYS + 1 + rng.exponential(20.0))
            timings.append(min(days, 120.0))
            missed.append(True)
            continue

        mean, sigma = _mean_and_sigma(archetype, month, shock_month, recovers)
        days = float(rng.normal(mean, sigma))
        if month == blip_month:
            days += 5.0
        days = max(_EARLIEST, min(days, float(MISSED_THRESHOLD_DAYS)))
        timings.append(round(days, 2))
        missed.append(False)

    return PaymentSeries(
        days_to_pay=tuple(timings), missed=tuple(missed), shock_month=shock_month, recovers=recovers
    )
