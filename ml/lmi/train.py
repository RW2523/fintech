"""Training the early-warning models (docs/07 §4.4).

Four models, one per horizon, each answering the same question at a different
distance: will this member be late in the next 7, 30, 60 or 90 days.

They are trained on a member-month panel with a time-based split. Never a random
split: a member appears in twenty rows, and shuffling puts the same member's
March in training and their April in the hold-out, which is the model reading
its own answer. The split is by date, so the hold-out is a period the model has
not seen at all.

Each model is calibrated on a block after training and before the hold-out, and
its prediction intervals are fitted on that same block. Fitting either on the
hold-out would make the hold-out numbers a description of the fitting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import numpy as np

from ml.common.metrics import Performance, evaluate
from ml.lmi.conformal import Intervals, band_report, coverage, fit_intervals
from ml.lmi.dataset import HORIZONS, Panel

__all__ = ["HorizonModel", "Split", "TrainedModels", "split_panel", "train_all", "train_horizon"]


@dataclass
class Split:
    """Three blocks of time, in order."""

    train_end: date
    calibrate_end: date

    def block(self, as_of: date) -> str:
        if as_of < self.train_end:
            return "train"
        if as_of < self.calibrate_end:
            return "calibrate"
        return "test"


def split_panel(panel: Panel, *, train_share: float = 0.6, calibrate_share: float = 0.2) -> Split:
    """Cut the panel by date, not at random.

    A member appears in twenty rows. A random split puts their March in
    training and their April in the hold-out, and the model reads its own
    answer.
    """
    dates = panel.as_of_dates
    train_end = dates[max(1, int(len(dates) * train_share))]
    calibrate_end = dates[max(2, int(len(dates) * (train_share + calibrate_share)))]
    return Split(train_end=train_end, calibrate_end=calibrate_end)


def _matrix(rows: list[Any], names: list[str]) -> np.ndarray:
    return np.asarray([[float(row.features.get(name, 0.0)) for name in names] for row in rows], dtype=float)


@dataclass
class HorizonModel:
    """One horizon's model, with everything needed to serve and judge it."""

    horizon: int
    features: list[str]
    model: Any = None
    calibrator: Any = None
    intervals: Intervals | None = None
    train: Performance | None = None
    calibrate: Performance | None = None
    test: Performance | None = None
    coverage: float = 0.0
    positives: dict[str, int] = field(default_factory=dict)
    rows: dict[str, int] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)

    def predict(self, matrix: np.ndarray) -> np.ndarray:
        raw = self.model.predict_proba(matrix)[:, 1]
        if self.calibrator is None:
            return np.asarray(raw, dtype=float)
        return np.asarray(self.calibrator.predict(raw), dtype=float)

    def as_dict(self) -> dict[str, Any]:
        return {
            "horizon_days": self.horizon,
            "features": self.features,
            "rows": self.rows,
            "positives": self.positives,
            "train": self.train.as_dict() if self.train else None,
            "calibrate": self.calibrate.as_dict() if self.calibrate else None,
            "test": self.test.as_dict() if self.test else None,
            "intervals": self.intervals.as_dict() if self.intervals else None,
            "interval_coverage": self.coverage,
            **self.detail,
        }


def train_horizon(panel: Panel, horizon: int, split: Split, *, seed: int = 20260906) -> HorizonModel:
    """One horizon, trained, calibrated and given intervals."""
    import lightgbm as lgb
    from sklearn.isotonic import IsotonicRegression

    names = panel.feature_names()
    blocks: dict[str, list[Any]] = {"train": [], "calibrate": [], "test": []}
    for row in panel.rows:
        if horizon not in row.labels:
            continue
        blocks[split.block(row.as_of)].append(row)

    model = HorizonModel(horizon=horizon, features=names)
    model.rows = {name: len(rows) for name, rows in blocks.items()}
    model.positives = {name: sum(row.labels[horizon] for row in rows) for name, rows in blocks.items()}

    if not blocks["train"] or model.positives["train"] == 0:
        model.detail["skipped"] = "no positive outcome in the training block"
        return model

    x_train = _matrix(blocks["train"], names)
    y_train = np.asarray([row.labels[horizon] for row in blocks["train"]], dtype=int)

    model.model = lgb.LGBMClassifier(
        n_estimators=300,
        learning_rate=0.05,
        num_leaves=15,
        min_child_samples=50,
        subsample=0.8,
        subsample_freq=1,
        colsample_bytree=0.8,
        reg_lambda=1.0,
        random_state=seed,
        verbose=-1,
    )
    model.model.fit(x_train, y_train)

    # --- calibration, on a block after training and before the hold-out ----
    if blocks["calibrate"] and model.positives["calibrate"] > 0:
        x_cal = _matrix(blocks["calibrate"], names)
        y_cal = np.asarray([row.labels[horizon] for row in blocks["calibrate"]], dtype=int)
        raw_cal = model.model.predict_proba(x_cal)[:, 1]
        # Isotonic rather than Platt: the relationship between a boosted
        # score and the outcome rate is monotone but not a logistic curve, and
        # forcing it into one leaves the tails wrong.
        model.calibrator = IsotonicRegression(out_of_bounds="clip").fit(raw_cal, y_cal)

        calibrated = model.predict(x_cal)
        model.calibrate = evaluate(y_cal, calibrated)
        # The intervals are fitted on the same block, which the model has not
        # been trained on. Fitting them on the hold-out would make the hold-out
        # coverage a description of the fitting.
        model.intervals = fit_intervals(
            list(calibrated),
            list(y_cal),
            periods=[row.as_of for row in blocks["calibrate"]],
        )

    # --- what it does on each block ----------------------------------------
    model.train = evaluate(y_train, model.predict(x_train))
    if blocks["test"] and model.positives["test"] > 0:
        x_test = _matrix(blocks["test"], names)
        y_test = np.asarray([row.labels[horizon] for row in blocks["test"]], dtype=int)
        predicted = model.predict(x_test)
        model.test = evaluate(y_test, predicted)
        if model.intervals is not None:
            model.coverage = coverage(model.intervals, list(predicted), list(y_test))
            model.detail["bands"] = band_report(model.intervals, list(predicted), list(y_test))

    model.detail["importance"] = _importance(model)
    model.detail["clean_only"] = _clean_only(model, blocks["test"], names)
    model.detail["simplest_rule"] = _simplest_rule(blocks["test"], horizon)
    return model


def _clean_only(model: HorizonModel, rows: list[Any], names: list[str]) -> dict[str, Any] | None:
    """How the model does on members who are not already late.

    The number that matters. A model asked to sort a book where a fifth of
    members are currently late will score well by recognising them, and that is
    not early warning: it is reading the present. The members worth catching
    are the ones who look fine today.
    """
    clean = [row for row in rows if row.features.get("late_now", 0.0) == 0.0]
    events = sum(row.labels[model.horizon] for row in clean)
    if len(clean) < 100 or events < 20:
        return {"rows": len(clean), "events": events, "measured": False}

    x = _matrix(clean, names)
    y = np.asarray([row.labels[model.horizon] for row in clean], dtype=int)
    performance = evaluate(y, model.predict(x))
    return {
        "rows": len(clean),
        "events": int(events),
        "event_rate": round(float(events) / len(clean), 4),
        "measured": True,
        **performance.as_dict(),
    }


def _simplest_rule(rows: list[Any], horizon: int) -> dict[str, Any] | None:
    """What one feature achieves on its own, for comparison.

    Reported beside the model so a reader can see what the rest of it adds. A
    model that beats "were they late in the last ninety days" by a hundredth is
    a model whose complexity has to justify itself.
    """
    usable = [row for row in rows if "days_late_p95_90d" in row.features]
    if len(usable) < 100:
        return None
    y = np.asarray([row.labels[horizon] for row in usable], dtype=int)
    if y.sum() == 0 or y.sum() == len(y):
        return None
    values = np.asarray([row.features["days_late_p95_90d"] for row in usable], dtype=float)
    span = values.max() - values.min()
    scaled = (values - values.min()) / span if span > 0 else values * 0
    return {"feature": "days_late_p95_90d", "auc": evaluate(y, scaled).as_dict()["auc"]}


def _importance(model: HorizonModel) -> list[dict[str, Any]]:
    """Which features the model leaned on, heaviest first."""
    if model.model is None:
        return []
    weights = list(model.model.feature_importances_)
    ranked = sorted(zip(model.features, weights, strict=True), key=lambda pair: -pair[1])
    total = sum(weights) or 1
    return [
        {"feature": name, "importance": round(float(weight) / total, 4)}
        for name, weight in ranked[:15]
        if weight > 0
    ]


@dataclass
class TrainedModels:
    version: str
    trained_at: str
    horizons: dict[int, HorizonModel] = field(default_factory=dict)
    panel: dict[str, Any] = field(default_factory=dict)
    survival: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "trained_at": self.trained_at,
            "panel": self.panel,
            "horizons": {str(h): model.as_dict() for h, model in sorted(self.horizons.items())},
            "survival": self.survival,
        }


def train_all(
    panel: Panel,
    *,
    version: str,
    seed: int = 20260906,
    train_share: float = 0.6,
    calibrate_share: float = 0.2,
) -> TrainedModels:
    """Every horizon, from one panel and one split."""
    split = split_panel(panel, train_share=train_share, calibrate_share=calibrate_share)
    trained = TrainedModels(
        version=version,
        trained_at=datetime.now(UTC).isoformat(),
        panel={
            "rows": len(panel.rows),
            "members": panel.members,
            "as_of_dates": [d.isoformat() for d in panel.as_of_dates],
            "features": panel.feature_names(),
            "dropped": dict(panel.dropped),
            "split": {
                "train_before": split.train_end.isoformat(),
                "calibrate_before": split.calibrate_end.isoformat(),
            },
        },
    )
    for horizon in HORIZONS:
        trained.horizons[horizon] = train_horizon(panel, horizon, split, seed=seed)
    return trained


def summary(trained: TrainedModels) -> str:
    """A table somebody can read without opening the JSON.

    The last two columns are the ones that matter. `clean` is the model's
    discrimination among members who are not already late, which is what early
    warning means; `1 feat` is what a single feature achieves on the whole
    hold-out, which is what the rest of the model has to beat.
    """
    lines = [
        f"{'horizon':>8} {'rows':>8} {'events':>8} {'AUC':>7} {'slope':>7} "
        f"{'cover':>7} {'clean':>7} {'1 feat':>7}"
    ]
    for horizon, model in sorted(trained.horizons.items()):
        test = model.test
        clean = model.detail.get("clean_only") or {}
        simple = model.detail.get("simplest_rule") or {}
        lines.append(
            f"{horizon:>7}d {model.rows.get('test', 0):>8} {model.positives.get('test', 0):>8} "
            f"{test.auc if test else 0:>7.4f} "
            f"{test.calibration_slope if test else 0:>7.3f} "
            f"{model.coverage:>7.1%} "
            f"{clean.get('auc', 0):>7.4f} "
            f"{simple.get('auc', 0):>7.4f}"
        )
    return "\n".join(lines)


def to_json(trained: TrainedModels) -> str:
    return json.dumps(trained.as_dict(), indent=2, default=str)
