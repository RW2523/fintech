"""T-062 — prediction intervals (docs/07 §4.4).

The interval has to answer the question an officer has, which is what share of
members scored like this one really do go late. These tests are about it
answering that rather than a question nobody asked.
"""

from __future__ import annotations

from ml.lmi.conformal import band_of, coverage, fit_intervals, wilson


def test_a_rate_interval_never_reaches_below_zero() -> None:
    """The normal approximation on 3 events in 400 does, and an interval that
    includes an impossible value tells the reader the method is wrong even when
    the number is right."""
    low, high = wilson(3, 400)
    assert low >= 0.0
    assert low < 3 / 400 < high


def test_a_wider_sample_gives_a_tighter_interval() -> None:
    narrow = wilson(30, 3000)
    wide = wilson(3, 300)
    assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])


def test_an_empty_band_admits_everything() -> None:
    assert wilson(0, 0) == (0.0, 1.0)


def test_bands_are_contiguous_and_cover_the_range() -> None:
    for value in (0.0, 0.019, 0.02, 0.5, 0.99, 1.0):
        assert band_of(value)


def test_a_band_with_too_few_members_is_not_reported() -> None:
    """Its observed rate would be noise presented as a measurement."""
    intervals = fit_intervals([0.9] * 5 + [0.01] * 500, [1] * 5 + [0] * 500)
    assert "0.70-1.01" not in intervals.bands
    assert "0.00-0.02" in intervals.bands


def test_the_interval_brackets_the_rate_it_was_fitted_on() -> None:
    predictions = [0.3] * 400
    outcomes = [1] * 120 + [0] * 280
    intervals = fit_intervals(predictions, outcomes)

    interval = intervals.for_probability(0.3)
    assert interval.lower < 0.30 < interval.upper


def test_an_unfitted_band_says_it_knows_nothing() -> None:
    """Rather than inventing a width."""
    intervals = fit_intervals([0.01] * 200, [0] * 200)
    interval = intervals.for_probability(0.95)
    assert (interval.lower, interval.upper) == (0.0, 1.0)
    assert interval.n == 0


def test_drift_between_periods_widens_the_interval() -> None:
    """The same band means a different rate in March and in June. An interval
    built from sampling alone is confident about the wrong thing: on this data
    it covered 4.5% of a later block against a 90% nominal."""
    predictions = [0.3] * 600
    # Same band, three months, rates 0.1, 0.3 and 0.5.
    outcomes = [1] * 20 + [0] * 180 + [1] * 60 + [0] * 140 + [1] * 100 + [0] * 100
    periods = ["2026-01"] * 200 + ["2026-02"] * 200 + ["2026-03"] * 200

    without = fit_intervals(predictions, outcomes)
    with_drift = fit_intervals(predictions, outcomes, periods=periods)

    assert with_drift.bands["0.20-0.40"].lower < without.bands["0.20-0.40"].lower
    assert with_drift.bands["0.20-0.40"].upper > without.bands["0.20-0.40"].upper
    assert with_drift.bands["0.20-0.40"].periods == 3


def test_coverage_is_measured_against_the_rate_not_one_outcome() -> None:
    """Covering a coin flip 90% of the time takes an interval about 0.9 wide,
    and that number is worthless to a reader."""
    predictions = [0.3] * 400
    outcomes = [1] * 120 + [0] * 280
    intervals = fit_intervals(predictions, outcomes)

    # A hold-out with the same rate is covered.
    assert coverage(intervals, [0.3] * 200, [1] * 60 + [0] * 140) == 1.0
    # One with a wildly different rate is not.
    assert coverage(intervals, [0.3] * 200, [1] * 190 + [0] * 10) == 0.0
