"""Seasonal adjustment for payment timing (docs/07 §4.2).

A member paid by an employer that runs payroll a day late every December is not
deteriorating in December. Without adjustment the platform would raise the same
alert on the same people every year, and officers would learn to ignore it.

STL needs a couple of years of monthly points before it can separate a season
from a trend, so below that the series is returned unchanged and says so.
Adjusting a short series invents a season out of noise and then subtracts it,
which is worse than not adjusting at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

__all__ = ["MIN_POINTS", "Adjusted", "adjust", "monthly_series"]

#: docs/07 §4.2 — two full cycles. One year of monthly points cannot tell a
#: season from a trend, because there is nothing to compare the season with.
MIN_POINTS = 24


@dataclass(frozen=True, slots=True)
class Adjusted:
    """A series with its season removed, or the reason it was not."""

    months: list[date]
    observed: list[float]
    residual: list[float]
    seasonal: list[float]
    adjusted: bool
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "adjusted": self.adjusted,
            "reason": self.reason,
            "points": len(self.observed),
            "months": [month.isoformat() for month in self.months],
            "residual": [round(value, 4) for value in self.residual],
            "seasonal": [round(value, 4) for value in self.seasonal],
        }


def monthly_series(points: list[tuple[date, float]]) -> tuple[list[date], list[float]]:
    """One value per month, the median of that month's observations.

    The median rather than the mean: one instalment paid thirty days late
    should not move a month whose other three were on time, and this series is
    the input to a change-point detector that would treat the jump as a level
    shift.
    """
    buckets: dict[date, list[float]] = {}
    for at, value in points:
        buckets.setdefault(date(at.year, at.month, 1), []).append(value)

    months = sorted(buckets)
    values = []
    for month in months:
        ordered = sorted(buckets[month])
        middle = len(ordered) // 2
        values.append(
            float(ordered[middle]) if len(ordered) % 2 else float(ordered[middle - 1] + ordered[middle]) / 2.0
        )
    return months, values


def adjust(points: list[tuple[date, float]], *, period: int = 12) -> Adjusted:
    """Remove the seasonal component, or explain why it was left in."""
    months, values = monthly_series(points)

    if len(values) < MIN_POINTS:
        return Adjusted(
            months=months,
            observed=values,
            residual=list(values),
            seasonal=[0.0] * len(values),
            adjusted=False,
            reason=f"{len(values)} monthly points; STL needs {MIN_POINTS}",
        )

    try:
        import numpy as np
        from statsmodels.tsa.seasonal import STL
    except ImportError as exc:  # pragma: no cover - the ml extra is absent
        return Adjusted(
            months=months,
            observed=values,
            residual=list(values),
            seasonal=[0.0] * len(values),
            adjusted=False,
            reason=f"statsmodels is not installed ({exc})",
        )

    result = STL(np.asarray(values, dtype=float), period=period, robust=True).fit()
    seasonal = [float(value) for value in result.seasonal]
    return Adjusted(
        months=months,
        observed=values,
        # Trend plus remainder: the level and the surprise, with the season
        # taken out. A change-point detector run on this sees a member who
        # changed, not a member whose employer always pays late in December.
        residual=[value - season for value, season in zip(values, seasonal, strict=True)],
        seasonal=seasonal,
        adjusted=True,
    )
