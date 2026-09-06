"""Independent validation of a trained credit-risk version (docs/12 §5).

    uv run python -m ml.credit_risk.validate [--version 2026.09.1]

Training reports its own numbers. This re-derives them from the saved
artifacts against a freshly built frame, saves the calibration plots, checks
the card says what a card must say, and states plainly which acceptance
criteria are met. It is deliberately a separate entry point: a model that can
only be checked by the code that built it has not been checked.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ml.common import registry
from ml.common.metrics import evaluate
from ml.credit_risk.dataset import LABEL, build_sync
from ml.credit_risk.predict import CreditRiskModel

FAMILY = "credit_risk"

#: docs/00 T-031 acceptance.
MIN_HOLDOUT_AUC = 0.72
CALIBRATION_BAND = (0.9, 1.1)

#: docs/12 §5 names the fields a card must carry.
REQUIRED_CARD_SECTIONS = ("Purpose", "Data", "Features", "Metrics", "Limitations", "Fairness", "Rollback")


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    detail: str

    def line(self) -> str:
        return f"  [{'PASS' if self.passed else 'FAIL'}] {self.name}: {self.detail}"


def _plot(path: Path, y: np.ndarray, p: np.ndarray, title: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    order = np.argsort(p)
    groups = np.array_split(order, min(10, max(2, len(order) // 40)))
    predicted = [float(p[g].mean()) for g in groups]
    observed = [float(y[g].mean()) for g in groups]
    sizes = [len(g) for g in groups]

    figure, axes = plt.subplots(figsize=(4.5, 4.5), dpi=140)
    top = max(max(predicted), max(observed)) * 1.15 + 1e-3
    axes.plot([0, top], [0, top], linewidth=1, linestyle="--", color="#888", label="perfect calibration")
    axes.scatter(
        predicted,
        observed,
        s=[max(12, n / 4) for n in sizes],
        color="#1f4e79",
        zorder=3,
        label="decile of predicted risk",
    )
    axes.set_xlim(0, top)
    axes.set_ylim(0, top)
    axes.set_xlabel("predicted probability of default")
    axes.set_ylabel("observed default rate")
    axes.set_title(title, fontsize=10)
    axes.legend(fontsize=7, loc="upper left")
    figure.tight_layout()
    figure.savefig(path)
    plt.close(figure)


def run(version: str | None = None, *, quiet: bool = False) -> dict[str, Any]:
    artifacts = registry.load(FAMILY, version)
    model = CreditRiskModel(artifacts)
    if not quiet:
        print(f"Validating {FAMILY}/{artifacts.version}")
        print("Rebuilding the frame from the member timeline ...")
    frame = build_sync(progress=not quiet)

    features = artifacts["features"]
    results: dict[str, Any] = {"version": artifacts.version, "splits": {}}
    checks: list[Check] = []

    for name in ("train", "valid", "test"):
        part = frame.split(name)
        y = part[LABEL].to_numpy(dtype=int)
        p = artifacts["calibrator"].transform(artifacts["champion"].score(part[features]))
        results["splits"][name] = evaluate(y, p).as_dict()
        _plot(artifacts.path / f"calibration_{name}.png", y, p, f"Champion calibration — {name}")

    held = results["splits"]["test"]
    trained = artifacts.metrics["champion"]["performance"]["test"]

    checks.append(
        Check(
            "reproduces the trained metrics",
            abs(held["auc"] - trained["auc"]) < 1e-6,
            f"revalidated AUC {held['auc']:.4f} against {trained['auc']:.4f} in the card",
        )
    )
    checks.append(
        Check(
            "hold-out AUC",
            held["auc"] >= MIN_HOLDOUT_AUC,
            f"{held['auc']:.4f} against a floor of {MIN_HOLDOUT_AUC} "
            f"(95% CI [{held['auc_ci_low']:.3f}, {held['auc_ci_high']:.3f}])",
        )
    )
    checks.append(
        Check(
            "hold-out calibration slope",
            CALIBRATION_BAND[0] <= held["calibration_slope"] <= CALIBRATION_BAND[1],
            f"{held['calibration_slope']:.3f} against a band of {CALIBRATION_BAND[0]}-{CALIBRATION_BAND[1]}",
        )
    )

    challenger = artifacts.metrics["challenger"]["performance"]["test"]
    checks.append(
        Check(
            "challenger hold-out AUC",
            challenger["auc"] >= MIN_HOLDOUT_AUC,
            f"{challenger['auc']:.4f} against a floor of {MIN_HOLDOUT_AUC}",
        )
    )

    fairness = artifacts.metrics["fairness"]
    checks.append(
        Check(
            "no protected characteristic among the features",
            not fairness["protected_features_present"],
            f"{fairness['protected_features_present'] or 'none found'}",
        )
    )

    card = (artifacts.path / "card.md").read_text()
    absent = [s for s in REQUIRED_CARD_SECTIONS if f"## {s}" not in card]
    checks.append(
        Check(
            "model card sections",
            not absent,
            f"missing {absent}" if absent else f"all {len(REQUIRED_CARD_SECTIONS)} present",
        )
    )

    # Scoring the same case twice must give the same run id, or a decision
    # could not be shown to be reconstructable.
    sample = frame.split("test").iloc[0].to_dict()
    first, second = model.predict(sample), model.predict(sample)
    checks.append(
        Check(
            "reproducible model run id",
            first.model_run_id == second.model_run_id and first.champion.pd_12m == second.champion.pd_12m,
            first.model_run_id,
        )
    )

    # Monotone directions must survive training, not merely be requested.
    negative = [
        c["feature"] for c in artifacts.metrics["champion"]["table"]["characteristics"] if c["weight"] < 0
    ]
    checks.append(
        Check(
            "policy directions held",
            not negative,
            f"{negative} carry a negative weight"
            if negative
            else "every characteristic weight is non-negative",
        )
    )

    results["checks"] = [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks]
    results["passed"] = all(c.passed for c in checks)
    (artifacts.path / "validation.json").write_text(json.dumps(results, indent=2) + "\n")

    if not quiet:
        print(f"\nChecks against {artifacts.path}:")
        for check in checks:
            print(check.line())
        print(f"\nCalibration plots written to {artifacts.path}")
        print("Overall:", "PASS" if results["passed"] else "FAIL (see above)")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=None)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--strict", action="store_true", help="exit non-zero when any check fails")
    args = parser.parse_args(argv)
    warnings.filterwarnings("ignore", category=FutureWarning)
    results = run(args.version, quiet=args.quiet)
    return 1 if args.strict and not results["passed"] else 0


if __name__ == "__main__":
    sys.exit(main())
