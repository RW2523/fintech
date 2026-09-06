"""Time to the next late payment, and time back from one (docs/07 §4.4).

The horizon models answer "will this happen in the next N days". Survival
answers "when", which is a different question and the one that decides whether
an officer calls this week or next month.

Two models. Time to first late, from the day a member's behaviour was last
clean. And time to cure, from the day they first went late, which is what says
whether an intervention is working or whether the member is drifting further.

Cox proportional hazards, because the coefficient of each covariate is a hazard
ratio a person can read: "this member's savings stopped, which multiplies the
hazard by 1.4" is a sentence an officer can act on and argue with. A boosted
survival model would fit better and say nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

__all__ = [
    "MAX_FOLLOW_UP_DAYS",
    "SurvivalFit",
    "SurvivalRow",
    "cure_rows",
    "fit_cox",
    "late_rows",
]

#: How long a member is followed before the observation is cut off. Anything
#: longer is a member whose outcome the data cannot speak to.
MAX_FOLLOW_UP_DAYS = 365

#: More than this many days past due is late, as everywhere else.
LATE_DAYS = 7


@dataclass(frozen=True, slots=True)
class SurvivalRow:
    """One member's time to an event, or to the day we stopped watching."""

    member_id: str
    duration_days: int
    observed: bool
    covariates: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "member_id": self.member_id,
            "duration_days": self.duration_days,
            "observed": self.observed,
            **self.covariates,
        }


def late_rows(
    book: dict[str, Any], *, as_of: date, last_observed: date, features: dict[str, dict[str, float]]
) -> list[SurvivalRow]:
    """Time from `as_of` to each member's next late settlement.

    A member who never goes late inside the follow-up window is censored, not
    a zero. Treating them as "never" would tell the model that most members are
    immune, when what the data says is that we stopped watching.
    """
    rows: list[SurvivalRow] = []
    horizon = min(last_observed, as_of + timedelta(days=MAX_FOLLOW_UP_DAYS))

    for member_id, (events, _, _) in book.items():
        covariates = features.get(member_id)
        if not covariates:
            continue

        when: date | None = None
        for event in events:
            if not (as_of < event.due_date <= horizon):
                continue
            timing = event.days_to_pay
            if timing is None or timing > LATE_DAYS:
                when = event.due_date
                break

        if when is not None:
            rows.append(SurvivalRow(member_id, (when - as_of).days, True, dict(covariates)))
        else:
            rows.append(SurvivalRow(member_id, (horizon - as_of).days, False, dict(covariates)))
    return rows


def cure_rows(
    book: dict[str, Any], *, last_observed: date, features: dict[str, dict[str, float]]
) -> list[SurvivalRow]:
    """Time from a member's first late settlement to their next on-time one.

    The number that says whether an intervention worked. A member still late at
    the end of the window is censored: they have not failed to cure, they have
    not cured yet.
    """
    rows: list[SurvivalRow] = []

    for member_id, (events, _, _) in book.items():
        covariates = features.get(member_id)
        if not covariates:
            continue

        first_late: date | None = None
        cured: date | None = None
        for event in events:
            timing = event.days_to_pay
            if first_late is None:
                if timing is not None and timing > LATE_DAYS:
                    first_late = event.due_date
                continue
            if timing is not None and timing <= LATE_DAYS:
                cured = event.due_date
                break

        if first_late is None:
            continue
        end = cured or min(last_observed, first_late + timedelta(days=MAX_FOLLOW_UP_DAYS))
        rows.append(
            SurvivalRow(member_id, max((end - first_late).days, 1), cured is not None, dict(covariates))
        )
    return rows


@dataclass
class SurvivalFit:
    """A fitted Cox model and what it says."""

    name: str
    n: int
    events: int
    concordance: float = 0.0
    hazard_ratios: list[dict[str, Any]] = field(default_factory=list)
    median_survival_days: float | None = None
    model: Any = None
    fitted: bool = False
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "n": self.n,
            "events": self.events,
            "fitted": self.fitted,
            "reason": self.reason,
            "concordance": round(self.concordance, 4),
            "median_survival_days": self.median_survival_days,
            "hazard_ratios": self.hazard_ratios,
        }


#: Covariates the Cox model is given. Deliberately few: a proportional-hazards
#: model with thirty correlated covariates produces hazard ratios nobody can
#: read, which defeats the reason for choosing it over something that fits
#: better.
COVARIATES = (
    "days_to_pay_median_90d",
    "days_late_p95_90d",
    "savings_slope_180d",
    "deduction_missed_count_90d",
    "streak_beyond_habit",
)


def fit_cox(rows: list[SurvivalRow], *, name: str, penalizer: float = 0.1) -> SurvivalFit:
    """Fit, and report the hazard ratio of each covariate."""
    events = sum(1 for row in rows if row.observed)
    fit = SurvivalFit(name=name, n=len(rows), events=events)

    if events < 30 or len(rows) < 100:
        fit.reason = f"{events} events in {len(rows)} rows; too few to fit"
        return fit

    try:
        import pandas as pd
        from lifelines import CoxPHFitter
    except ImportError as exc:  # pragma: no cover - the ml extra is absent
        fit.reason = f"lifelines is not installed ({exc})"
        return fit

    columns = [name for name in COVARIATES if any(name in row.covariates for row in rows)]
    frame = pd.DataFrame(
        [
            {
                "duration": row.duration_days,
                "observed": int(row.observed),
                **{name: row.covariates.get(name, 0.0) for name in columns},
            }
            for row in rows
        ]
    )
    # A covariate that never varies has no hazard ratio, and lifelines will not
    # invert the matrix if one is left in.
    constant = [name for name in columns if frame[name].nunique() <= 1]
    frame = frame.drop(columns=constant)

    model = CoxPHFitter(penalizer=penalizer)
    try:
        model.fit(frame, duration_col="duration", event_col="observed")
    except Exception as exc:  # pragma: no cover - degenerate covariates
        fit.reason = f"the fit did not converge ({exc})"
        return fit

    fit.model = model
    fit.fitted = True
    fit.concordance = float(model.concordance_index_)
    fit.median_survival_days = (
        float(model.baseline_survival_.index[(model.baseline_survival_.iloc[:, 0] <= 0.5).argmax()])
        if (model.baseline_survival_.iloc[:, 0] <= 0.5).any()
        else None
    )
    fit.hazard_ratios = [
        {
            "covariate": covariate,
            "hazard_ratio": round(float(model.hazard_ratios_[covariate]), 4),
            "p_value": round(float(model.summary.loc[covariate, "p"]), 5),
        }
        for covariate in model.hazard_ratios_.index
    ]
    if constant:
        fit.reason = f"dropped constant covariates: {', '.join(constant)}"
    return fit
