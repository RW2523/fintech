"""The early-warning panel (docs/07 §4.4).

One row per member per as-of month: what their behaviour looked like on that
day, and whether they went late in the next 7, 30, 60 or 90 days.

Two rules shape all of it.

Nothing after the as-of date may reach the features. That sounds obvious and is
easy to break: a baseline computed over a member's whole history contains the
very deterioration the model is asked to predict, and a model trained on it
scores beautifully and predicts nothing. Every feature here is computed from
events strictly before the as-of date.

And late means the platform's own first rung, more than seven days past due,
not merely after the due date. In this population the median member pays a day
early and half settle a day or two on, so counting any positive day count as
late would make almost every row positive and the model meaningless.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ml.lmi.families import (
    Deduction,
    SavingsPoint,
    deduction_features,
    savings_features,
)
from ml.lmi.temporal import (
    DueEvent,
    baseline_of,
    days_to_pay,
    late_streak,
    robust_z,
    window_features,
)

__all__ = [
    "HORIZONS",
    "LATE_DAYS",
    "MIN_HISTORY_EVENTS",
    "Panel",
    "PanelRow",
    "build_panel",
    "features_at",
    "label_at",
]

#: docs/07 §4.4 — how far ahead each model looks, in days.
HORIZONS = (7, 30, 60, 90)

#: More than this many days past due is late. `core.outcome.late7`, and the
#: same definition the change-point evaluation uses.
LATE_DAYS = 7

#: Below this a member has no habit to depart from, and a row about them would
#: teach the model to predict from noise.
MIN_HISTORY_EVENTS = 6


@dataclass(frozen=True, slots=True)
class PanelRow:
    member_id: str
    as_of: date
    features: dict[str, float]
    labels: dict[int, int]
    #: Horizons whose window extends past the end of the data. A row is not
    #: labelled for those: a member who has not had the chance to go late is
    #: not a member who did not.
    censored: tuple[int, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "member_id": self.member_id,
            "as_of": self.as_of.isoformat(),
            **{f"y_{horizon}d": self.labels.get(horizon) for horizon in HORIZONS},
            **self.features,
        }


@dataclass
class Panel:
    rows: list[PanelRow] = field(default_factory=list)
    as_of_dates: list[date] = field(default_factory=list)
    members: int = 0
    #: What was left out, and why. A dataset that drops rows silently is a
    #: dataset nobody can check.
    dropped: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def feature_names(self) -> list[str]:
        """Only features present on every row.

        A feature some rows lack would be imputed, and an imputed value is a
        made-up observation the model cannot tell from a real one.
        """
        shared: set[str] | None = None
        for row in self.rows:
            keys = set(row.features)
            shared = keys if shared is None else (shared & keys)
        return sorted(shared or set())


def features_at(
    events: list[DueEvent],
    deductions: list[Deduction],
    savings: list[SavingsPoint],
    *,
    as_of: date,
) -> dict[str, float] | None:
    """A member's features on a day, from what happened before it.

    Returns None when there is too little history to say anything, rather than
    a row of zeros: zeros would teach the model that a new member looks like a
    member with nothing wrong.
    """
    past = [event for event in events if event.due_date < as_of]
    if len(days_to_pay(past)) < MIN_HISTORY_EVENTS:
        return None

    features: dict[str, float] = {}
    features.update(window_features(past, as_of=as_of))
    features.update(deduction_features([d for d in deductions if d.cycle < as_of], as_of=as_of))
    features.update(savings_features([s for s in savings if s.at < as_of], as_of=as_of))

    # The departure from this member's own habit, which is the signal the whole
    # engine exists for. Computed over the history before the as-of date, so
    # the baseline cannot contain the deterioration being predicted.
    timings = [float(value) for value in days_to_pay(past)]
    baseline = baseline_of("days_to_pay", timings)
    for window in (30, 90):
        key = f"days_to_pay_median_{window}d"
        if key in features:
            features[f"{key}_robust_z"] = robust_z(features[key], baseline)
    features["baseline_median"] = round(baseline.median, 3)
    features["baseline_scale"] = round(baseline.scale, 3)
    features["history_events"] = float(len(timings))

    # The streak measured against this member's own habit, not against the due
    # date. It is the feature that separates a member who has always paid three
    # days on from one who has just started to.
    features["streak_beyond_habit"] = float(
        late_streak(past, tolerance_days=round(baseline.median) if baseline.usable else 0)
    )

    # Whether they are late right now. Carried explicitly because the model's
    # headline discrimination is mostly this, and a reader has to be able to
    # see how much the rest adds.
    features["late_now"] = 1.0 if features.get("late_streak", 0.0) > 0 else 0.0
    return features


def label_at(events: list[DueEvent], *, as_of: date, horizon: int, last_observed: date) -> int | None:
    """Whether the member went late in the next `horizon` days.

    None when the window runs past the end of the data. A member who has not
    had the chance to go late is not a member who did not, and counting them
    as a clean row teaches the model that recent members never deteriorate.
    """
    end = as_of + timedelta(days=horizon)
    if end > last_observed:
        return None

    for event in events:
        if not (as_of < event.due_date <= end):
            continue
        timing = event.days_to_pay
        if timing is None or timing > LATE_DAYS:
            return 1
    return 0


def build_panel(
    histories: dict[str, tuple[list[DueEvent], list[Deduction], list[SavingsPoint]]],
    *,
    as_of_dates: list[date],
    last_observed: date,
) -> Panel:
    """One row per member per as-of date, for every member with a history."""
    panel = Panel(as_of_dates=sorted(as_of_dates), members=len(histories))

    for member_id, (events, deductions, savings) in histories.items():
        for as_of in panel.as_of_dates:
            features = features_at(events, deductions, savings, as_of=as_of)
            if features is None:
                panel.dropped["too_little_history"] += 1
                continue

            labels: dict[int, int] = {}
            censored: list[int] = []
            for horizon in HORIZONS:
                label = label_at(events, as_of=as_of, horizon=horizon, last_observed=last_observed)
                if label is None:
                    censored.append(horizon)
                else:
                    labels[horizon] = label

            if not labels:
                panel.dropped["fully_censored"] += 1
                continue

            panel.rows.append(
                PanelRow(
                    member_id=member_id,
                    as_of=as_of,
                    features=features,
                    labels=labels,
                    censored=tuple(censored),
                )
            )

    return panel


def month_ends(start: date, end: date) -> list[date]:
    """The first of each month between two dates, inclusive.

    As-of dates are month starts rather than arbitrary days so a member appears
    once a month, which keeps the panel balanced: sampling every day would give
    long-tenured members thirty times the weight of new ones.
    """
    out: list[date] = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        out.append(cursor)
        cursor = date(cursor.year + (cursor.month // 12), cursor.month % 12 + 1, 1)
    return out
