"""Discrimination and calibration metrics (docs/07 §2.2, docs/12 §5).

A credit model is judged on two separate questions. Does it rank borrowers
correctly (AUC, PR-AUC, KS, decile lift)? And are its probabilities the right
size (Brier, calibration slope and intercept)? A model can be excellent at one
and useless at the other, so the card always reports both.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

#: Probabilities are clipped before any logit, because a predicted 0 or 1 makes
#: the calibration regression undefined and one such row would destroy the fit.
_EPS = 1e-6


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), _EPS, 1.0 - _EPS)
    return np.log(p / (1.0 - p))


def ks_statistic(y: np.ndarray, p: np.ndarray) -> float:
    """The widest gap between the cumulative bad and good distributions."""
    order = np.argsort(-np.asarray(p, dtype=float))
    y = np.asarray(y, dtype=float)[order]
    bad, good = y.sum(), (1.0 - y).sum()
    if bad == 0 or good == 0:
        return float("nan")
    return float(np.max(np.abs(np.cumsum(y) / bad - np.cumsum(1.0 - y) / good)))


def calibration_slope_intercept(y: np.ndarray, p: np.ndarray) -> tuple[float, float]:
    """Regress the outcome on the predicted log-odds.

    A slope of 1 with an intercept of 0 means the predictions are the right
    size. A slope below 1 means they are too spread out; above 1, too timid.
    """
    from sklearn.linear_model import LogisticRegression

    y = np.asarray(y, dtype=int)
    if len(np.unique(y)) < 2:
        return float("nan"), float("nan")
    x = _logit(p).reshape(-1, 1)
    fit = LogisticRegression(C=np.inf, solver="lbfgs", max_iter=1000).fit(x, y)
    return float(fit.coef_[0][0]), float(fit.intercept_[0])


def decile_lift(y: np.ndarray, p: np.ndarray, *, deciles: int = 10) -> list[dict[str, Any]]:
    """Event rate per predicted-risk decile, worst first."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    if len(y) < deciles:
        return []
    order = np.argsort(-p)
    base = y.mean()
    rows: list[dict[str, Any]] = []
    for index, chunk in enumerate(np.array_split(order, deciles), start=1):
        rate = float(y[chunk].mean())
        rows.append(
            {
                "decile": index,
                "n": len(chunk),
                "events": int(y[chunk].sum()),
                "rate": round(rate, 5),
                "lift": round(float(rate / base), 3) if base > 0 else None,
                "mean_pd": round(float(p[chunk].mean()), 5),
            }
        )
    return rows


def auc_confidence_interval(
    y: np.ndarray, p: np.ndarray, *, draws: int = 2000, seed: int = 20260906
) -> tuple[float, float]:
    """A bootstrap interval for AUC.

    Reported because a point estimate on a few dozen events reads as far more
    precise than it is, and the hold-out here is exactly that small.
    """
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    rng = np.random.default_rng(seed)
    scores: list[float] = []
    for _ in range(draws):
        pick = rng.integers(0, len(y), len(y))
        if len(np.unique(y[pick])) < 2:
            continue
        scores.append(float(roc_auc_score(y[pick], p[pick])))
    if not scores:
        return float("nan"), float("nan")
    return float(np.percentile(scores, 2.5)), float(np.percentile(scores, 97.5))


@dataclass(frozen=True, slots=True)
class Performance:
    """Everything docs/07 §2.2 requires a model card to state."""

    n: int
    events: int
    event_rate: float
    auc: float
    auc_ci_low: float
    auc_ci_high: float
    pr_auc: float
    ks: float
    brier: float
    calibration_slope: float
    calibration_intercept: float
    deciles: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate(y: np.ndarray, p: np.ndarray, *, deciles: int = 10) -> Performance:
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    single_class = len(np.unique(y)) < 2
    low, high = (float("nan"), float("nan")) if single_class else auc_confidence_interval(y, p)
    slope, intercept = calibration_slope_intercept(y, p)
    return Performance(
        n=len(y),
        events=int(y.sum()),
        event_rate=round(float(y.mean()), 5) if len(y) else float("nan"),
        auc=float("nan") if single_class else round(float(roc_auc_score(y, p)), 4),
        auc_ci_low=round(low, 4) if low == low else low,
        auc_ci_high=round(high, 4) if high == high else high,
        pr_auc=float("nan") if single_class else round(float(average_precision_score(y, p)), 4),
        ks=round(ks_statistic(y, p), 4),
        brier=round(float(brier_score_loss(y, p)), 5),
        calibration_slope=round(slope, 4) if slope == slope else slope,
        calibration_intercept=round(intercept, 4) if intercept == intercept else intercept,
        deciles=decile_lift(y, p, deciles=deciles),
    )
