"""Train the credit-risk champion and challenger (docs/07 §2.2).

    uv run python -m ml.credit_risk.train

Writes `ml/credit_risk/artifacts/<version>/` holding the champion scorecard,
the LightGBM challenger, the calibrator, the out-of-distribution detector, a
model card and the metrics behind it. Artifacts are write-once: retraining
adds a version beside the old one so a decision made last year can still be
reconstructed from the model that made it.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from ml.common import registry
from ml.common.metrics import evaluate
from ml.credit_risk import card
from ml.credit_risk import scorecard as sc
from ml.credit_risk.calibration import Calibrator, grades_of
from ml.credit_risk.dataset import APPLICATION_ONLY, LABEL, Frame, build_sync
from ml.credit_risk.explain import monotone_table
from ml.credit_risk.ood import OutOfDistribution

FAMILY = "credit_risk"

#: The champion is selected on how well it *ranks*, because calibration is a
#: separate fitted stage that follows it. Judging the uncalibrated scorecard on
#: a probability score would penalise it for the very thing the calibrator
#: exists to fix.
#:
#: A search five times this size was run and chose the same configuration, and
#: hold-out discrimination stayed within a thousandth across all of it. The
#: grid is kept small because widening it buys nothing but minutes.
CHAMPION_GRID: dict[str, list[Any]] = {
    "bins": [5, 8, 10],
    "max_features": [3, 4, 5, 6, 8],
    "l2": [0.5, 1.0, 2.0, 5.0],
    "smoothing": [1.0, 10.0, 50.0],
    "min_iv": [0.05, 0.1],
    "max_correlation": [0.7, 0.9],
}

#: docs/07 §2.2 names `num_leaves 15`. The search keeps it and offers a smaller
#: tree as well, because sixty-odd defaults will not fill fifteen leaves.
CHALLENGER_GRID: dict[str, list[Any]] = {
    "num_leaves": [7, 15],
    "learning_rate": [0.02, 0.05],
    "min_child_weight": [20, 40],
    "feature_fraction": [0.6, 0.9],
}

#: A single validation block of a few dozen events picks a winner mostly by
#: luck. Averaging it with walk-forward folds inside the training period buys
#: a steadier estimate without ever showing the model a later month first.
INNER_FOLD_FROM = 4
SELECTION_WEIGHT_INNER = 0.5


@dataclass
class Champion:
    """The scorecard and how it was arrived at."""

    model: sc.Scorecard
    hyperparameters: dict[str, Any]
    inner_auc: float
    valid_auc: float


def _fit_scorecard(
    frame: pd.DataFrame, features: list[str], monotone: dict[str, int], **hp: Any
) -> sc.Scorecard | None:
    sc._SMOOTHING = hp["smoothing"]
    y = frame[LABEL].to_numpy(dtype=int)
    binning = sc.fit_binning(frame[features], y, monotone=monotone, bins=hp["bins"])
    woe = binning.transform(frame[features])
    chosen = sc.select(
        binning,
        woe,
        max_features=hp["max_features"],
        min_iv=hp["min_iv"],
        max_correlation=hp["max_correlation"],
    )
    if not chosen:
        return None
    weights, intercept = sc.fit_scorecard(woe, y, chosen, l2=hp["l2"])
    # The non-negativity bound drives collinear characteristics to exactly
    # zero. A characteristic that contributes nothing is not part of the
    # scorecard, and listing it on the card would suggest it was consulted.
    # Dropping it changes no score, since its term is zero either way.
    kept = [(name, weight) for name, weight in zip(chosen, weights, strict=True) if weight > 0]
    if not kept:
        return None
    names = [name for name, _ in kept]
    return sc.Scorecard(
        binning=binning,
        features=names,
        weights=np.array([w for _, w in kept], dtype=float),
        intercept=intercept,
        baseline_woe={name: float(woe[name].mean()) for name in names},
    )


def select_champion(
    train: pd.DataFrame,
    valid: pd.DataFrame,
    features: list[str],
    monotone: dict[str, int],
    *,
    quiet: bool = False,
) -> Champion:
    cohorts = sorted(train["cohort"].unique())
    folds = [
        (train[train["cohort"].isin(cohorts[:k])], train[train["cohort"] == cohorts[k]])
        for k in range(INNER_FOLD_FROM, len(cohorts))
    ]
    keys = list(CHAMPION_GRID)
    best: Champion | None = None

    for combination in itertools.product(*(CHAMPION_GRID[k] for k in keys)):
        hp = dict(zip(keys, combination, strict=True))
        inner: list[float] = []
        for fold_train, fold_test in folds:
            model = _fit_scorecard(fold_train, features, monotone, **hp)
            if model is None or fold_test[LABEL].nunique() < 2:
                continue
            inner.append(float(roc_auc_score(fold_test[LABEL], model.predict_proba(fold_test[features]))))
        model = _fit_scorecard(train, features, monotone, **hp)
        if model is None or not inner:
            continue
        valid_auc = float(roc_auc_score(valid[LABEL], model.predict_proba(valid[features])))
        candidate = Champion(
            model=model, hyperparameters=hp, inner_auc=float(np.mean(inner)), valid_auc=valid_auc
        )
        if best is None or _criterion(candidate) > _criterion(best):
            best = candidate

    if best is None:
        raise RuntimeError("no champion could be fitted; check the training frame")
    if not quiet:
        print(
            f"  champion: {best.hyperparameters} "
            f"inner={best.inner_auc:.4f} valid={best.valid_auc:.4f} "
            f"({len(best.model.features)} characteristics)"
        )
    return best


def _criterion(candidate: Champion) -> float:
    return SELECTION_WEIGHT_INNER * candidate.inner_auc + (1.0 - SELECTION_WEIGHT_INNER) * candidate.valid_auc


@dataclass
class Challenger:
    """A LightGBM booster held to the same policy directions."""

    booster: Any
    features: list[str]
    hyperparameters: dict[str, Any]
    valid_auc: float

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        x = frame[self.features].apply(pd.to_numeric, errors="coerce")
        return np.asarray(self.booster.predict(x), dtype=float)


def train_challenger(
    train: pd.DataFrame,
    valid: pd.DataFrame,
    features: list[str],
    monotone: dict[str, int],
    *,
    seed: int = 20260906,
    quiet: bool = False,
) -> Challenger:
    import lightgbm as lgb

    numeric = [f for f in features if train[f].dtype != object]
    constraints = [monotone.get(f, 0) for f in numeric]

    def frame_of(part: pd.DataFrame) -> pd.DataFrame:
        return part[numeric].apply(pd.to_numeric, errors="coerce")

    keys = list(CHALLENGER_GRID)
    best: Challenger | None = None
    for combination in itertools.product(*(CHALLENGER_GRID[k] for k in keys)):
        hp = dict(zip(keys, combination, strict=True))
        params = {
            "objective": "binary",
            "verbose": -1,
            "seed": seed,
            "monotone_constraints": constraints,
            # The advanced method keeps the constraint without the heavy
            # accuracy cost the basic method pays on shallow trees.
            "monotone_constraints_method": "advanced",
            "bagging_fraction": 0.8,
            "bagging_freq": 1,
            **hp,
        }
        booster = lgb.train(
            params,
            lgb.Dataset(frame_of(train), train[LABEL].to_numpy(dtype=int)),
            num_boost_round=800,
            valid_sets=[lgb.Dataset(frame_of(valid), valid[LABEL].to_numpy(dtype=int))],
            callbacks=[lgb.early_stopping(60, verbose=False)],
        )
        auc = float(roc_auc_score(valid[LABEL], booster.predict(frame_of(valid))))
        candidate = Challenger(
            booster=booster,
            features=numeric,
            hyperparameters={**hp, "best_iteration": booster.best_iteration},
            valid_auc=auc,
        )
        if best is None or candidate.valid_auc > best.valid_auc:
            best = candidate

    assert best is not None
    if not quiet:
        print(f"  challenger: {best.hyperparameters} valid={best.valid_auc:.4f}")
    return best


def _grade_distribution(pd_values: np.ndarray, y: np.ndarray) -> list[dict[str, Any]]:
    grades = grades_of(pd_values)
    rows: list[dict[str, Any]] = []
    for grade in ("A", "B", "C", "D", "E"):
        mask = grades == grade
        rows.append(
            {
                "grade": grade,
                "n": int(mask.sum()),
                "events": int(y[mask].sum()) if mask.any() else 0,
                "observed_rate": round(float(y[mask].mean()), 5) if mask.any() else None,
                "mean_pd": round(float(pd_values[mask].mean()), 5) if mask.any() else None,
            }
        )
    return rows


def _fairness_report(frame: Frame, features: list[str]) -> dict[str, Any]:
    """docs/12 §5 — the check is structural, not statistical.

    No protected characteristic is collected anywhere in the platform, so the
    test is that none has crept into the feature set, and that no feature is
    acting as a stand-in for one.
    """
    forbidden = (
        "ethnic",
        "religio",
        "gender",
        "sex",
        "health",
        "disabil",
        "political",
        "marital",
        "race",
        "nationality",
        "age",
        "dob",
    )
    present = sorted({word for name in features for word in forbidden if word in name.casefold()})
    proxies: list[dict[str, Any]] = []
    rows = frame.rows
    if "employer_sector" in rows:
        for name in features:
            if rows[name].dtype == object:
                continue
            values = pd.to_numeric(rows[name], errors="coerce")
            if values.notna().sum() < 30 or values.nunique() < 3:
                continue
            spread = values.groupby(rows["employer_sector"]).mean()
            if len(spread) > 1 and values.std(ddof=0) > 0:
                ratio = float(spread.std(ddof=0) / values.std(ddof=0))
                if ratio > 0.5:
                    proxies.append({"feature": name, "sector_spread_ratio": round(ratio, 3)})
    return {
        "protected_features_present": present,
        "method": "structural: no protected characteristic is collected by any service",
        "proxy_scan": {
            "grouping": "employer_sector",
            "flagged": proxies,
            "threshold": 0.5,
            "note": "employer sector is a model input in its own right, "
            "so a high ratio marks correlation to review, not a breach",
        },
    }


def _performance(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    return evaluate(y, p).as_dict()


def _calibration_diagnostic(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    """Separate a calibration miss into level and spread.

    A slope away from 1 can mean two different faults. Either the predictions
    sit at the wrong level, which a fresh intercept fixes, or they are spread
    too wide, which means the model claims more separation than the period
    supports. Shifting the log-odds until the mean prediction matches the
    observed rate removes the first, so whatever slope remains is the second.
    """
    from scipy.optimize import brentq

    from ml.common.metrics import calibration_slope_intercept

    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, dtype=int)
    served_slope, served_intercept = calibration_slope_intercept(y, p)
    log_odds = np.log(p / (1 - p))
    try:
        shift = float(
            brentq(lambda d: float((1 / (1 + np.exp(-(log_odds + d)))).mean()) - float(y.mean()), -8.0, 8.0)
        )
    except ValueError:
        return {"served_slope": served_slope, "level_corrected_slope": None, "level_shift_log_odds": None}
    corrected, _ = calibration_slope_intercept(y, 1 / (1 + np.exp(-(log_odds + shift))))
    return {
        "served_slope": round(served_slope, 4) if served_slope == served_slope else None,
        "served_intercept": round(served_intercept, 4) if served_intercept == served_intercept else None,
        "level_corrected_slope": round(corrected, 4) if corrected == corrected else None,
        "level_shift_log_odds": round(shift, 4),
        "mean_pd": round(float(p.mean()), 5),
        "observed_rate": round(float(y.mean()), 5),
    }


def run(
    *,
    limit: int | None = None,
    version: str | None = None,
    set_latest: bool = True,
    quiet: bool = False,
) -> tuple[Path, dict[str, Any]]:
    started = datetime.now(UTC)
    if not quiet:
        print("Building the training frame from the member timeline ...")
    frame = build_sync(limit=limit, progress=not quiet)

    monotone = monotone_table()
    features = [
        name
        for name in frame.feature_names
        if name not in APPLICATION_ONLY
        and frame.rows[name].notna().any()
        and frame.rows[name].nunique(dropna=True) > 1
    ]
    dropped = [n for n in frame.feature_names if n not in features]

    train, valid, test = (frame.split(s) for s in ("train", "valid", "test"))
    if not quiet:
        print(f"  {len(features)} usable features; dropped {dropped or 'none'}")
        print(
            f"  train {len(train)}/{train[LABEL].sum()} · valid {len(valid)}/"
            f"{valid[LABEL].sum()} · test {len(test)}/{test[LABEL].sum()}"
        )
        print("Fitting ...")

    champion = select_champion(train, valid, features, monotone, quiet=quiet)
    challenger = train_challenger(train, valid, features, monotone, quiet=quiet)

    calibrator = Calibrator.fit(champion.model.score(valid[features]), valid[LABEL].to_numpy(dtype=int))
    challenger_calibrator = Calibrator.fit(
        np.log(
            np.clip(challenger.predict(valid), 1e-6, 1 - 1e-6)
            / (1 - np.clip(challenger.predict(valid), 1e-6, 1 - 1e-6))
        ),
        valid[LABEL].to_numpy(dtype=int),
    )
    ood = OutOfDistribution.fit(train, [f for f in features if train[f].dtype != object])

    def champion_pd(part: pd.DataFrame) -> np.ndarray:
        return calibrator.transform(champion.model.score(part[features]))

    def challenger_pd(part: pd.DataFrame) -> np.ndarray:
        raw = np.clip(challenger.predict(part), 1e-6, 1 - 1e-6)
        return challenger_calibrator.transform(np.log(raw / (1 - raw)))

    splits = {"train": train, "valid": valid, "test": test}
    metrics: dict[str, Any] = {
        "family": FAMILY,
        "trained_at": started.isoformat(),
        "dataset": frame.summary,
        "features_used": features,
        "features_dropped": dropped,
        "champion": {
            "kind": "woe_scorecard_constrained_logistic",
            "hyperparameters": champion.hyperparameters,
            "selection": {
                "inner_auc": round(champion.inner_auc, 4),
                "valid_auc": round(champion.valid_auc, 4),
                "criterion": "0.5 * walk-forward inner AUC + 0.5 * validation AUC",
            },
            "characteristics": champion.model.features,
            "table": champion.model.table(),
            "calibration": calibrator.parameters,
            "performance": {
                name: _performance(part[LABEL].to_numpy(dtype=int), champion_pd(part))
                for name, part in splits.items()
            },
            "grades": {
                name: _grade_distribution(champion_pd(part), part[LABEL].to_numpy(dtype=int))
                for name, part in splits.items()
            },
            "calibration_diagnostic": {
                name: _calibration_diagnostic(part[LABEL].to_numpy(dtype=int), champion_pd(part))
                for name, part in splits.items()
            },
        },
        "challenger": {
            "kind": "lightgbm_monotone",
            "hyperparameters": challenger.hyperparameters,
            "features": challenger.features,
            "calibration": challenger_calibrator.parameters,
            "performance": {
                name: _performance(part[LABEL].to_numpy(dtype=int), challenger_pd(part))
                for name, part in splits.items()
            },
        },
        "ood": {
            "kind": "isolation_forest",
            "features": ood.features,
            "train_score_mean": round(float(ood.score(train).mean()), 4),
            "test_score_mean": round(float(ood.score(test).mean()), 4),
        },
        "fairness": _fairness_report(frame, features),
        "information_values": champion.model.binning.information_values(),
    }

    version = version or registry.next_version(FAMILY)
    body = card.render(version=version, metrics=metrics)
    path = registry.save(
        FAMILY,
        version,
        objects={
            "champion": champion.model,
            "challenger": challenger.booster,
            "challenger_features": challenger.features,
            "calibrator": calibrator,
            "challenger_calibrator": challenger_calibrator,
            "ood": ood,
            "features": features,
        },
        metrics=metrics,
        card=body,
    )
    if set_latest:
        registry.set_latest(FAMILY, version)

    if not quiet:
        held = metrics["champion"]["performance"]["test"]
        print(f"\nWrote {path}")
        print(
            f"  hold-out champion  AUC {held['auc']:.4f} "
            f"[{held['auc_ci_low']:.3f}, {held['auc_ci_high']:.3f}] "
            f"slope {held['calibration_slope']:.3f}"
        )
        gone = metrics["challenger"]["performance"]["test"]
        print(
            f"  hold-out challenger AUC {gone['auc']:.4f} "
            f"[{gone['auc_ci_low']:.3f}, {gone['auc_ci_high']:.3f}] "
            f"slope {gone['calibration_slope']:.3f}"
        )
    return path, metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=None, help="train on the first N accounts only (smoke test)"
    )
    parser.add_argument("--version", default=None, help="artifact version to write")
    parser.add_argument(
        "--no-latest", action="store_true", help="write the version but leave serving pointed where it is"
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    warnings.filterwarnings("ignore", category=FutureWarning)
    _, metrics = run(limit=args.limit, version=args.version, set_latest=not args.no_latest, quiet=args.quiet)
    if not args.quiet:
        print(
            json.dumps(
                {
                    "champion_test": metrics["champion"]["performance"]["test"]["auc"],
                    "challenger_test": metrics["challenger"]["performance"]["test"]["auc"],
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
