"""T-061 — change-point detection (docs/07 §4.3).

CUSUM answers the question this engine exists for: not "is this value unusual"
but "has the small drift accumulated into a change". These tests build series
by hand so the accumulation can be followed.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.changepoint import (
    Alarm,
    SignalConfig,
    confirmation_window,
    cusum,
    detect,
    lead_days,
    pelt_breaks,
)

START = date(2025, 1, 1)
TIMING = SignalConfig(signal="days_to_pay")


def series(values: list[float], *, start: date = START, every: int = 30) -> list[tuple[date, float]]:
    return [(start + timedelta(days=every * i), value) for i, value in enumerate(values)]


# ---------------------------------------------------------------------------
# what CUSUM is for
# ---------------------------------------------------------------------------
def test_a_steady_member_never_alarms() -> None:
    assert cusum(series([0.0] * 24), TIMING) == []


def test_noise_around_the_habit_never_alarms() -> None:
    """Half a deviation per observation is noise, and an engine that fires on
    it is an engine officers stop reading."""
    noisy = [0.4, -0.3, 0.5, -0.5, 0.4, -0.2, 0.3, -0.4] * 3
    assert cusum(series(noisy), TIMING) == []


def test_a_small_persistent_drift_accumulates_into_an_alarm() -> None:
    """The point of CUSUM. Each value is well inside anything a threshold would
    catch, and together they are unmistakable."""
    drifting = [0.0] * 6 + [1.0] * 10
    alarms = cusum(series(drifting), TIMING)
    assert alarms, "a persistent drift went undetected"
    assert alarms[0].statistic > TIMING.threshold


def test_one_loud_month_does_not_alarm_on_its_own() -> None:
    """A single spike is a late payment, not a change in behaviour."""
    spike = [0.0] * 10 + [3.0] + [0.0] * 10
    assert cusum(series(spike), TIMING) == []


def test_an_improvement_does_not_alarm() -> None:
    """A member who starts paying earlier than usual has changed too, and
    nobody needs an alert about it."""
    improving = [0.0] * 5 + [-2.0] * 12
    assert cusum(series(improving), TIMING) == []


def test_a_falling_savings_balance_alarms_when_the_signal_watches_down() -> None:
    savings = SignalConfig(signal="savings_balance", direction="down")
    assert cusum(series([0.0] * 5 + [-1.0] * 12), savings)
    assert cusum(series([0.0] * 5 + [1.0] * 12), savings) == []


def test_two_changes_produce_two_dates() -> None:
    """A member who changed in March and again in July needs both. The second
    is what an officer needs when the first was actioned."""
    twice = [0.0] * 4 + [1.2] * 8 + [0.0] * 6 + [1.2] * 8
    alarms = cusum(series(twice), TIMING)
    assert len(alarms) >= 2
    assert alarms[0].at < alarms[1].at


def test_the_threshold_is_configurable_per_signal() -> None:
    lenient = SignalConfig(signal="x", threshold=1.0)
    strict = SignalConfig(signal="x", threshold=20.0)
    drifting = series([0.0] * 4 + [1.0] * 8)
    assert cusum(drifting, lenient)
    assert cusum(drifting, strict) == []


# ---------------------------------------------------------------------------
# confirmation
# ---------------------------------------------------------------------------
def test_pelt_finds_a_level_shift() -> None:
    pytest.importorskip("ruptures")
    breaks = pelt_breaks([0.0] * 12 + [6.0] * 12)
    assert breaks, "PELT saw no break in an obvious level shift"
    assert any(9 <= index <= 15 for index in breaks)


def test_pelt_finds_nothing_in_a_flat_series() -> None:
    pytest.importorskip("ruptures")
    assert pelt_breaks([1.0] * 24) == []


def test_a_short_series_is_not_segmented() -> None:
    assert pelt_breaks([1.0, 2.0, 3.0]) == []


def test_two_methods_agreeing_confirms_the_change() -> None:
    pytest.importorskip("ruptures")
    z = [0.0] * 8 + [1.5] * 12
    raw = [0.0] * 8 + [6.0] * 12
    alarms = detect(series(z), TIMING, raw=raw)

    assert alarms
    assert alarms[0].confirmed, "PELT saw the same shift and the alarm is unconfirmed"
    assert abs((alarms[0].confirmed_at - alarms[0].at).days) <= confirmation_window(
        [at for at, _ in series(z)]
    )
    # The two dates are months apart, which is the point of keeping both.
    assert alarms[0].detected_at > alarms[0].at


def test_an_unconfirmed_alarm_is_still_reported() -> None:
    """It is the confirmation that is missing, not the alarm. Dropping it would
    make a detector that only works when two methods agree, which is a
    different and weaker detector."""
    z = [0.0] * 4 + [1.0] * 10
    alarms = detect(series(z), TIMING, raw=[0.0] * 14)
    assert alarms
    assert alarms[0].confirmed is False


# ---------------------------------------------------------------------------
# lead time
# ---------------------------------------------------------------------------
def test_lead_time_is_measured_to_the_first_late_event() -> None:
    """A detection after the first late payment is not early warning, it is
    bookkeeping."""
    alarm = Alarm(
        signal="x",
        at=date(2026, 3, 1),
        detected_at=date(2026, 3, 1),
        statistic=5.0,
        threshold=4.0,
        observations=10,
    )
    events = [(date(2026, 2, 1), 0), (date(2026, 4, 12), 5)]
    assert lead_days(alarm, events) == 42


def test_a_member_who_never_went_late_has_no_lead_time() -> None:
    """Which is not a miss: it is a member who did not need the warning."""
    alarm = Alarm(
        signal="x",
        at=date(2026, 3, 1),
        detected_at=date(2026, 3, 1),
        statistic=5.0,
        threshold=4.0,
        observations=10,
    )
    assert lead_days(alarm, [(date(2026, 4, 1), 0)]) is None


def test_an_alarm_after_the_event_reads_as_negative() -> None:
    alarm = Alarm(
        signal="x",
        at=date(2026, 5, 1),
        detected_at=date(2026, 5, 1),
        statistic=5.0,
        threshold=4.0,
        observations=10,
    )
    assert lead_days(alarm, [(date(2026, 4, 1), 7)]) == -30


def test_the_change_point_is_where_the_drift_began_not_where_the_sum_fired() -> None:
    """They are months apart on a monthly series, and an officer told the case
    changed in December when it changed in August would look at the wrong
    payslip."""
    z = [0.0] * 4 + [1.2] * 8
    alarms = cusum(series(z), TIMING)

    assert alarms
    assert alarms[0].at == series(z)[4][0], "the change began at the fifth observation"
    assert alarms[0].detected_at > alarms[0].at
    assert alarms[0].detection_lag_days > 60


def test_the_confirmation_window_scales_with_the_sampling() -> None:
    """A fixed window in days means different things on a weekly series and a
    monthly one, and on monthly instalments 30 days is a single observation."""
    from app.changepoint import CONFIRMATION_WINDOW_DAYS

    weekly = [START + timedelta(days=7 * i) for i in range(10)]
    monthly = [START + timedelta(days=30 * i) for i in range(10)]

    assert confirmation_window(weekly) == CONFIRMATION_WINDOW_DAYS
    assert confirmation_window(monthly) == 90
