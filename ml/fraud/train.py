"""Train the fraud anomaly detector (docs/07 §3).

    uv run python -m ml.fraud.train

An isolation forest over the case vector, saved with a card. The output is
advisory by design: it says a case is unlike the others, which is a reason for
a person to look at it, never a reason to doubt the applicant. The rules
decide severity; this only decides where attention goes first.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ml.common import registry
from ml.fraud.dataset import build
from ml.fraud.features import FEATURE_NAMES
from ml.fraud.ood import AnomalyDetector

FAMILY = "fraud"

#: docs/07 §3 — at or above this the case gets an advisory finding.
ADVISORY_THRESHOLD = 0.7

#: The share of cases expected to clear the threshold. Set from the training
#: distribution rather than assumed: a detector that flags a third of the queue
#: is a detector nobody reads.
TARGET_FLAG_RATE = 0.05

CARD = """# Model card — Fraud anomaly detector

- **Version** `fraud/{version}`  ·  **Trained** {trained_at}  ·  **Owner** {owner}
- **Status** flags {flagged} of {n} training cases ({rate:.1%}) at a threshold of {threshold}

## Purpose
Mark a case as unlike the ones the platform has seen, so a person looks at it
sooner. It is advisory: docs/07 §3 states that an anomaly never raises the
case level on its own, and the fraud service enforces that by carrying the
finding as advisory and excluding advisory findings from the level.

## Data
{data}

## Features
{features}

## Metrics
{metrics}

## Limitations
{limitations}

## Fairness
No protected characteristic is collected by any service, and the feature
registry independently restricts what the fraud service may read to five of
its twenty-three features. Employer sector is not among them, so the detector
cannot key on it even indirectly.

## Rollback
`ml/fraud/artifacts/latest.txt` names the version in use. Write the previous
version into that file, or call `ml.common.registry.set_latest('fraud', ...)`,
and restart the service. Artifacts are write-once.
"""


def _percentile_table(scores: np.ndarray) -> dict[str, float]:
    return {f"p{p}": round(float(np.percentile(scores, p)), 4) for p in (5, 25, 50, 75, 90, 95, 99)}


def run(
    *, version: str | None = None, set_latest: bool = True, quiet: bool = False
) -> tuple[Path, dict[str, Any]]:
    started = datetime.now(UTC)
    if not quiet:
        print("Building the case frame from the corpus and the core record ...")
    frame = build()
    rows: pd.DataFrame = frame.rows

    detector = AnomalyDetector.fit(rows, list(FEATURE_NAMES))
    scores = detector.score(rows)
    flagged = int((scores >= ADVISORY_THRESHOLD).sum())

    metrics: dict[str, Any] = {
        "family": FAMILY,
        "trained_at": started.isoformat(),
        "dataset": frame.summary,
        "features": list(detector.features),
        "features_dropped": list(detector.dropped),
        "threshold": ADVISORY_THRESHOLD,
        "flagged": flagged,
        "flag_rate": round(flagged / len(rows), 5) if len(rows) else 0.0,
        "target_flag_rate": TARGET_FLAG_RATE,
        "score_percentiles": _percentile_table(scores),
        "anchors": {"low": round(detector.low, 5), "high": round(detector.high, 5)},
    }

    top = rows.assign(score=scores).nlargest(5, "score")
    metrics["most_unusual"] = [
        {
            "application_id": row.application_id,
            "score": round(float(row.score), 4),
            "guarantees_given": float(row.guarantees_given),
            "in_cycle": float(row.in_cycle),
            "missing_required": float(row.missing_required),
            "amount_to_salary": round(float(row.amount_to_salary), 2),
        }
        for row in top.itertuples()
    ]

    version = version or registry.next_version(FAMILY)
    card = CARD.format(
        version=version,
        trained_at=metrics["trained_at"],
        owner="Financial Crime, Cooperative Credit Institution (demo build)",
        flagged=flagged,
        n=len(rows),
        rate=metrics["flag_rate"],
        threshold=ADVISORY_THRESHOLD,
        data=(
            f"{len(rows)} applications from the generated corpus, with the "
            "guarantee graph from the core record. Synthetic throughout: no "
            "real member data exists in this build. Unlabelled, because "
            "there is no ground truth for 'a person a reviewer would want to "
            "see'; the forest learns the shape of the ordinary case instead."
        ),
        features=(
            "The five registry features whose permitted uses include "
            "FRAUD, plus case-file statistics and the member's position "
            "in the guarantee graph:\n\n"
            + "\n".join(f"- `{name}`" for name in detector.features)
            + (
                "\n\nNever varied while training, so dropped: "
                + ", ".join(f"`{n}`" for n in detector.dropped)
                if detector.dropped
                else ""
            )
        ),
        metrics=(
            "An unlabelled detector has no accuracy to quote. What can be "
            "stated is its selectivity:\n\n"
            f"- flags {flagged} of {len(rows)} training cases "
            f"({metrics['flag_rate']:.1%}) at a threshold of "
            f"{ADVISORY_THRESHOLD}\n"
            f"- score percentiles: {metrics['score_percentiles']}\n\n"
            "The five most unusual cases in training, for a reader to "
            "judge whether the detector is looking at the right things:\n\n"
            + "\n".join(
                f"- `{c['application_id']}` score {c['score']}, "
                f"{c['guarantees_given']:.0f} guarantees given, "
                f"in a cycle: {bool(c['in_cycle'])}, "
                f"{c['missing_required']:.0f} required documents "
                f"missing, borrowing {c['amount_to_salary']}x salary"
                for c in metrics["most_unusual"]
            )
        ),
        limitations=(
            "- **Unusual is not wrong.** The forest has no notion of fraud. It "
            "reports distance from the ordinary, and a first-time borrower with "
            "an incomplete file is unusual without having done anything.\n"
            "- **Advisory only.** docs/07 §3 forbids this score from raising a "
            "case level by itself, and the service carries the finding as "
            "advisory so it is excluded from the level and from the integrity "
            "deduction.\n"
            "- **Trained on the queue it will score.** The population is one "
            "generated cohort, so the detector describes that cohort. It must be "
            "refitted whenever the queue's shape changes.\n"
            "- **The case-file half is thin.** Document statistics come from the "
            "corpus manifest; extraction confidence and open findings are only "
            "populated once a case has been through the document pipeline."
        ),
    )

    path = registry.save(
        FAMILY,
        version,
        objects={"detector": detector, "features": list(detector.features)},
        metrics=metrics,
        card=card,
    )
    if set_latest:
        registry.set_latest(FAMILY, version)

    if not quiet:
        print(f"Wrote {path}")
        print(
            f"  flags {flagged}/{len(rows)} ({metrics['flag_rate']:.1%}) "
            f"at {ADVISORY_THRESHOLD}; target about {TARGET_FLAG_RATE:.0%}"
        )
        print(f"  percentiles {metrics['score_percentiles']}")
    return path, metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=None)
    parser.add_argument("--no-latest", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    warnings.filterwarnings("ignore", category=FutureWarning)
    run(version=args.version, set_latest=not args.no_latest, quiet=args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
