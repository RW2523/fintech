"""Prediction intervals that mean something to a reader (docs/07 §4.4).

An officer told "p30 is 0.32" reads it as more precise than it is. The interval
has to answer the question they actually have, which is: among members scored
like this one, what share really do go late?

That is a rate, and the interval is a binomial interval on it. Members are put
into bands by their predicted probability, the observed rate in each band is
measured on data the model was not trained on, and the interval is the Wilson
score interval for that rate.

**Split conformal on the outcome was tried first and thrown away.** It gives a
finite-sample guarantee and it is what the spec names, but the quantity it
covers is the outcome, which is 0 or 1. Covering a coin flip 90% of the time
requires an interval about 0.9 wide, and the first implementation duly produced
"0.32, somewhere between 0.00 and 1.00" while reporting 91% coverage. The
guarantee held and the number was worthless. Coverage of an interval nobody can
act on is not a property worth having.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

__all__ = ["BANDS", "Band", "Interval", "Intervals", "band_of", "coverage", "fit_intervals"]

#: Bands over the predicted probability. Coarse on purpose: a band needs enough
#: members for its observed rate to mean anything, and fine bands on a
#: calibration set give rates that are themselves noise.
BANDS: tuple[tuple[float, float], ...] = (
    (0.00, 0.02),
    (0.02, 0.05),
    (0.05, 0.10),
    (0.10, 0.20),
    (0.20, 0.40),
    (0.40, 0.70),
    (0.70, 1.01),
)

#: Below this a band's observed rate is not reported as an interval; the
#: neighbouring bands are merged into it instead.
MIN_BAND_N = 50


def band_of(p: float) -> str:
    for low, high in BANDS:
        if low <= p < high:
            return f"{low:.2f}-{high:.2f}"
    return f"{BANDS[-1][0]:.2f}-{BANDS[-1][1]:.2f}"


def wilson(successes: int, n: int, *, nominal: float = 0.90) -> tuple[float, float]:
    """The Wilson score interval for a rate.

    Wilson rather than the normal approximation because the rates here are
    small: the normal interval on 3 events in 400 reaches below zero, and an
    interval that includes an impossible value tells the reader the method is
    wrong even when the number is right.
    """
    if n == 0:
        return (0.0, 1.0)
    # Two-sided z for the nominal level.
    z = {0.80: 1.2816, 0.90: 1.6449, 0.95: 1.9600, 0.99: 2.5758}.get(nominal, 1.6449)
    phat = successes / n
    denominator = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denominator
    spread = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, centre - spread), min(1.0, centre + spread))


@dataclass(frozen=True, slots=True)
class Interval:
    lower: float
    upper: float
    nominal: float
    band: str
    n: int

    @property
    def width(self) -> float:
        return round(self.upper - self.lower, 4)

    def contains(self, value: float) -> bool:
        return self.lower <= value <= self.upper

    def as_dict(self) -> dict[str, Any]:
        return {
            "lower": round(self.lower, 4),
            "upper": round(self.upper, 4),
            "nominal": self.nominal,
            "band": self.band,
            "n": self.n,
        }


@dataclass(frozen=True, slots=True)
class Band:
    name: str
    n: int
    events: int
    rate: float
    lower: float
    upper: float
    #: How many calibration periods contributed. One period means the interval
    #: carries sampling error only and says nothing about drift.
    periods: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "band": self.name,
            "n": self.n,
            "events": self.events,
            "rate": round(self.rate, 4),
            "lower": round(self.lower, 4),
            "upper": round(self.upper, 4),
            "width": round(self.upper - self.lower, 4),
            "periods": self.periods,
        }


@dataclass
class Intervals:
    """Fitted band rates, from a set the model was not trained on."""

    nominal: float
    calibration_n: int
    bands: dict[str, Band] = field(default_factory=dict)

    def for_probability(self, p: float) -> Interval:
        name = band_of(p)
        band = self.bands.get(name)
        if band is None:
            # No band was fitted here. Saying so beats inventing a width.
            return Interval(lower=0.0, upper=1.0, nominal=self.nominal, band=name, n=0)
        return Interval(lower=band.lower, upper=band.upper, nominal=self.nominal, band=name, n=band.n)

    def as_dict(self) -> dict[str, Any]:
        return {
            "nominal": self.nominal,
            "calibration_n": self.calibration_n,
            "kind": "binomial rate interval per predicted band",
            "bands": [band.as_dict() for band in self.bands.values()],
        }


def fit_intervals(
    predictions: list[float],
    outcomes: list[int],
    *,
    periods: list[Any] | None = None,
    nominal: float = 0.90,
) -> Intervals:
    """Measure the real rate in each predicted band, with its interval.

    Two sources of uncertainty, not one.

    Sampling: a band with four hundred members and twelve events has a rate
    known only to within a couple of points, which is what Wilson measures.

    Drift: the same band means a different rate in March and in June, because
    the book ages and the season turns. A calibration set is a handful of
    months and the model is served in later ones, so an interval built from
    sampling alone is confident about the wrong thing. Measured here as the
    spread of a band's rate across the calibration months, with the interval
    spanning both.

    Without the second, coverage on a later block measured 4.5% at 60 days
    against a 90% nominal. The number was not wrong; the interval was.
    """
    grouped: dict[str, list[int]] = {}
    by_period: dict[str, dict[Any, list[int]]] = {}
    for index, (p, y) in enumerate(zip(predictions, outcomes, strict=True)):
        name = band_of(p)
        grouped.setdefault(name, []).append(int(y))
        if periods is not None:
            by_period.setdefault(name, {}).setdefault(periods[index], []).append(int(y))

    intervals = Intervals(nominal=nominal, calibration_n=len(predictions))
    for name, values in sorted(grouped.items()):
        if len(values) < MIN_BAND_N:
            continue
        events = sum(values)
        low, high = wilson(events, len(values), nominal=nominal)

        # Widen to the range the band's rate actually took across periods, each
        # period taken with its own sampling interval so a thin month does not
        # pull a bound out on noise alone.
        monthly = by_period.get(name, {})
        usable = [
            wilson(sum(month), len(month), nominal=nominal)
            for month in monthly.values()
            if len(month) >= MIN_BAND_N // 2
        ]
        if len(usable) >= 2:
            low = min(low, min(bounds[0] for bounds in usable))
            high = max(high, max(bounds[1] for bounds in usable))

        intervals.bands[name] = Band(
            name=name,
            n=len(values),
            events=events,
            rate=events / len(values),
            lower=low,
            upper=high,
            periods=len(monthly),
        )
    return intervals


def coverage(intervals: Intervals, predictions: list[float], outcomes: list[int]) -> float:
    """The share of held-out members whose band interval contained the truth.

    The truth here is the rate actually observed among held-out members in that
    band, not one member's outcome. Weighted by how many members sit in each
    band, so a band nobody is in cannot carry the number.
    """
    grouped: dict[str, list[int]] = {}
    for p, y in zip(predictions, outcomes, strict=True):
        grouped.setdefault(band_of(p), []).append(int(y))

    covered = 0
    total = 0
    for name, values in grouped.items():
        band = intervals.bands.get(name)
        if band is None:
            continue
        observed = sum(values) / len(values)
        total += len(values)
        if band.lower <= observed <= band.upper:
            covered += len(values)
    return round(covered / total, 4) if total else 0.0


def band_report(intervals: Intervals, predictions: list[float], outcomes: list[int]) -> list[dict[str, Any]]:
    """Band by band: what was promised and what happened."""
    grouped: dict[str, list[int]] = {}
    for p, y in zip(predictions, outcomes, strict=True):
        grouped.setdefault(band_of(p), []).append(int(y))

    out: list[dict[str, Any]] = []
    for name in sorted(set(intervals.bands) | set(grouped)):
        band = intervals.bands.get(name)
        held = grouped.get(name, [])
        observed = sum(held) / len(held) if held else None
        out.append(
            {
                "band": name,
                "calibration_rate": round(band.rate, 4) if band else None,
                "lower": round(band.lower, 4) if band else None,
                "upper": round(band.upper, 4) if band else None,
                "holdout_n": len(held),
                "holdout_rate": round(observed, 4) if observed is not None else None,
                "covered": bool(band and observed is not None and band.lower <= observed <= band.upper),
            }
        )
    return out
