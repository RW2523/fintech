"""T-062 — the early-warning panel (docs/07 §4.4).

Two rules shape the dataset, and both are easy to break silently: nothing after
the as-of date may reach the features, and late means the platform's own first
rung rather than any positive day count. These tests hold both.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from ml.lmi.dataset import (
    HORIZONS,
    LATE_DAYS,
    build_panel,
    features_at,
    label_at,
    month_ends,
)
from ml.lmi.temporal import DueEvent

Book = dict[str, tuple[list[DueEvent], list[Any], list[Any]]]

START = date(2025, 1, 1)


def monthly(timings: Sequence[int | None], *, start: date = START) -> list[DueEvent]:
    return [
        DueEvent(
            due_date=start + timedelta(days=30 * index),
            amount_due=250.0,
            paid_at=None if timing is None else start + timedelta(days=30 * index + timing),
            amount_paid=0.0 if timing is None else 250.0,
        )
        for index, timing in enumerate(timings)
    ]


# ---------------------------------------------------------------------------
# nothing after the as-of date
# ---------------------------------------------------------------------------
def test_features_ignore_everything_after_the_as_of_date() -> None:
    """A baseline computed over a member's whole history contains the very
    deterioration the model is asked to predict. A model trained on it scores
    beautifully and predicts nothing."""
    events = monthly([0] * 10 + [30] * 6)
    cutoff = START + timedelta(days=30 * 10)

    before = features_at(events, [], [], as_of=cutoff)
    assert before is not None
    assert before["days_to_pay_median_365d"] == 0.0, "the future leaked into the baseline"

    after = features_at(events, [], [], as_of=START + timedelta(days=30 * 16))
    assert after is not None
    assert after["days_to_pay_median_365d"] > 0.0


def test_a_member_with_too_little_history_yields_nothing_not_zeros() -> None:
    """Zeros would teach the model that a new member looks like a member with
    nothing wrong."""
    assert features_at(monthly([0, 0, 0]), [], [], as_of=START + timedelta(days=200)) is None


# ---------------------------------------------------------------------------
# what counts as late
# ---------------------------------------------------------------------------
def test_paying_a_day_after_the_due_date_is_not_late() -> None:
    """In this population the median member pays a day early and half settle a
    day or two on. Counting any positive day count as late would make almost
    every row positive and the model meaningless."""
    events = monthly([1, 2, 3])
    label = label_at(
        events,
        as_of=START - timedelta(days=1),
        horizon=90,
        last_observed=START + timedelta(days=400),
    )
    assert label == 0


def test_paying_beyond_the_first_rung_is_late() -> None:
    events = monthly([LATE_DAYS + 1])
    label = label_at(
        events,
        as_of=START - timedelta(days=1),
        horizon=30,
        last_observed=START + timedelta(days=400),
    )
    assert label == 1


def test_an_unpaid_instalment_is_late() -> None:
    events = monthly([None])
    label = label_at(
        events,
        as_of=START - timedelta(days=1),
        horizon=30,
        last_observed=START + timedelta(days=400),
    )
    assert label == 1


def test_only_events_inside_the_horizon_count() -> None:
    events = monthly([0, 0, 30])
    as_of = START - timedelta(days=1)
    last = START + timedelta(days=400)
    assert label_at(events, as_of=as_of, horizon=30, last_observed=last) == 0
    assert label_at(events, as_of=as_of, horizon=90, last_observed=last) == 1


def test_a_window_past_the_end_of_the_data_is_not_labelled() -> None:
    """A member who has not had the chance to go late is not a member who did
    not, and counting them clean teaches the model that recent members never
    deteriorate."""
    events = monthly([0, 0])
    assert label_at(events, as_of=START, horizon=90, last_observed=START + timedelta(days=10)) is None


# ---------------------------------------------------------------------------
# the panel
# ---------------------------------------------------------------------------
def test_a_panel_row_per_member_per_month() -> None:
    book: Book = {
        "M-1": (monthly([0] * 20), [], []),
        "M-2": (monthly([2] * 20), [], []),
    }
    dates = [START + timedelta(days=30 * i) for i in (8, 9, 10)]
    panel = build_panel(book, as_of_dates=dates, last_observed=START + timedelta(days=700))

    assert len(panel.rows) == 6
    assert {row.member_id for row in panel.rows} == {"M-1", "M-2"}


def test_a_censored_horizon_is_recorded_not_guessed() -> None:
    book: Book = {"M-1": (monthly([0] * 12), [], [])}
    as_of = START + timedelta(days=30 * 10)
    panel = build_panel(book, as_of_dates=[as_of], last_observed=as_of + timedelta(days=40))

    row = panel.rows[0]
    assert 7 in row.labels and 30 in row.labels
    assert 60 in row.censored and 90 in row.censored


def test_what_was_dropped_is_reported() -> None:
    """A dataset that drops rows silently is a dataset nobody can check."""
    book: Book = {"M-thin": (monthly([0, 0]), [], [])}
    panel = build_panel(
        book,
        as_of_dates=[START + timedelta(days=60)],
        last_observed=START + timedelta(days=700),
    )
    assert panel.rows == []
    assert panel.dropped["too_little_history"] == 1


def test_only_features_present_on_every_row_are_offered() -> None:
    """A feature some rows lack would be imputed, and an imputed value is a
    made-up observation the model cannot tell from a real one."""
    book: Book = {
        "M-1": (monthly([0] * 20), [], []),
        "M-2": (monthly([1] * 20), [], []),
    }
    dates = [START + timedelta(days=30 * i) for i in (10, 12)]
    panel = build_panel(book, as_of_dates=dates, last_observed=START + timedelta(days=700))

    names = panel.feature_names()
    for row in panel.rows:
        assert set(names) <= set(row.features)


def test_month_ends_are_month_starts_in_order() -> None:
    months = month_ends(date(2025, 11, 20), date(2026, 2, 3))
    assert months == [date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)]


def test_every_documented_horizon_is_built() -> None:
    assert HORIZONS == (7, 30, 60, 90)
