"""T-060 — seasonal adjustment (docs/07 §4.2).

A member paid by an employer that runs payroll a day late every December is not
deteriorating in December. Without adjustment the platform raises the same
alert on the same people every year, and officers learn to ignore it.
"""

from __future__ import annotations

from datetime import date

import pytest

from ml.lmi.seasonal import MIN_POINTS, adjust, monthly_series


def series(values: list[float], *, start_year: int = 2024) -> list[tuple[date, float]]:
    points = []
    for index, value in enumerate(values):
        year = start_year + index // 12
        month = index % 12 + 1
        points.append((date(year, month, 15), value))
    return points


def test_a_month_is_the_median_of_its_observations() -> None:
    """One instalment paid thirty days late should not move a month whose other
    three were on time: the change-point detector downstream would read the
    jump as a level shift."""
    points = [
        (date(2026, 3, 2), 0.0),
        (date(2026, 3, 10), 0.0),
        (date(2026, 3, 20), 30.0),
        (date(2026, 4, 2), 1.0),
    ]
    months, values = monthly_series(points)
    assert months == [date(2026, 3, 1), date(2026, 4, 1)]
    assert values == [0.0, 1.0]


def test_a_short_series_is_left_alone_and_says_so() -> None:
    """Adjusting a short series invents a season out of noise and then
    subtracts it, which is worse than not adjusting at all."""
    result = adjust(series([1.0] * 12))
    assert result.adjusted is False
    assert "STL needs" in (result.reason or "")
    assert result.residual == result.observed


def test_two_years_is_enough_to_separate_a_season_from_a_trend() -> None:
    pytest.importorskip("statsmodels")
    # Flat all year except a December that is always four days late.
    values = [4.0 if (index % 12) == 11 else 0.0 for index in range(MIN_POINTS)]
    result = adjust(series(values))

    assert result.adjusted is True
    decembers = [result.seasonal[index] for index in range(11, MIN_POINTS, 12)]
    assert all(value > 1.0 for value in decembers), "the December season was not found"


def test_the_december_spike_is_removed_from_the_residual() -> None:
    """Which is the whole point: the residual is what a change-point detector
    reads, and it must not contain a season."""
    pytest.importorskip("statsmodels")
    values = [4.0 if (index % 12) == 11 else 0.0 for index in range(MIN_POINTS)]
    result = adjust(series(values))

    december = result.residual[11]
    january = result.residual[0]
    assert abs(december - january) < 2.0, "December still stands out after adjustment"


def test_a_real_deterioration_survives_adjustment() -> None:
    """A member who genuinely got worse must still look worse, or the
    adjustment has removed the signal along with the season."""
    pytest.importorskip("statsmodels")
    values = [0.0] * 18 + [6.0] * 6
    result = adjust(series(values))

    assert result.adjusted is True
    assert result.residual[-1] - result.residual[0] > 2.0
