"""Train the early-warning models.

    uv run python -m ml.lmi --members 5000

Reads the population, builds the member-month panel, trains one model per
horizon, measures them on a period none of them saw, and writes the artifacts
and a card.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import timedelta
from pathlib import Path

from ml.common import registry
from ml.lmi.dataset import HORIZONS, build_panel, month_ends
from ml.lmi.load import database_url, load_book, observed_end
from ml.lmi.survival import cure_rows, fit_cox, late_rows
from ml.lmi.train import summary, train_all

ROOT = Path(__file__).resolve().parents[2]
FAMILY = "lmi_early_warning"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--members", type=int, default=0, help="cap for a quick run")
    parser.add_argument("--seed", type=int, default=20260906)
    parser.add_argument("--train-share", type=float, default=0.6)
    parser.add_argument("--calibrate-share", type=float, default=0.2)
    parser.add_argument("--dry-run", action="store_true", help="do not write artifacts")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    book, first_due, _ = asyncio.run(load_book(database_url(ROOT)))
    if args.members:
        book = dict(sorted(book.items())[: args.members])

    last = observed_end(book)
    print(f"  {len(book)} members, due events from {first_due} to {last}")

    # As-of dates stop a horizon short of the end, because a row whose window
    # runs past the data cannot be labelled and would otherwise be dropped
    # after the work of computing it.
    as_of_dates = [
        day
        for day in month_ends(first_due + timedelta(days=210), last)
        if day + timedelta(days=max(HORIZONS)) <= last
    ]
    print(f"  {len(as_of_dates)} as-of months from {as_of_dates[0]} to {as_of_dates[-1]}")

    panel = build_panel(book, as_of_dates=as_of_dates, last_observed=last)
    print(
        f"  {len(panel.rows)} panel rows, {len(panel.feature_names())} features, "
        f"dropped {dict(panel.dropped)}"
    )
    for horizon in HORIZONS:
        labelled = [row for row in panel.rows if horizon in row.labels]
        events = sum(row.labels[horizon] for row in labelled)
        rate = events / len(labelled) if labelled else 0
        print(f"    {horizon:>3}d: {len(labelled)} rows, {events} events ({rate:.2%})")

    # --- survival: when, not whether ---------------------------------------
    # Fitted at the last as-of date the panel covers, so the covariates are the
    # same features the horizon models were given at that point.
    anchor = as_of_dates[-1]
    covariates = {row.member_id: row.features for row in panel.rows if row.as_of == anchor}
    to_late = fit_cox(
        late_rows(book, as_of=anchor, last_observed=last, features=covariates),
        name="time_to_first_late",
    )
    to_cure = fit_cox(cure_rows(book, last_observed=last, features=covariates), name="time_to_cure")
    print(f"\n  survival at {anchor}")
    for fit in (to_late, to_cure):
        print(
            f"    {fit.name:<20} n {fit.n:>6} events {fit.events:>6} "
            f"concordance {fit.concordance:.4f}" + (f"  ({fit.reason})" if fit.reason else "")
        )
        for ratio in fit.hazard_ratios:
            print(f"      {ratio['covariate']:<32} HR {ratio['hazard_ratio']:>7.3f} p {ratio['p_value']:.4f}")

    version = registry.next_version(FAMILY)
    trained = train_all(
        panel,
        version=version,
        seed=args.seed,
        train_share=args.train_share,
        calibrate_share=args.calibrate_share,
    )
    trained.survival = {
        "anchor": anchor.isoformat(),
        "time_to_first_late": to_late.as_dict(),
        "time_to_cure": to_cure.as_dict(),
    }
    print(f"\n{summary(trained)}")
    print(f"\n  trained in {time.perf_counter() - started:.1f}s")

    if args.dry_run:
        return 0

    from ml.lmi.card import card_for

    objects: dict[str, object] = {}
    for horizon, model in trained.horizons.items():
        if model.model is not None:
            objects[f"horizon_{horizon}"] = model.model
        if model.calibrator is not None:
            objects[f"calibrator_{horizon}"] = model.calibrator
        if model.intervals is not None:
            objects[f"intervals_{horizon}"] = model.intervals
    for fit in (to_late, to_cure):
        if fit.model is not None:
            objects[fit.name] = fit.model

    registry.save(
        FAMILY,
        version,
        objects=objects,
        metrics=trained.as_dict(),
        card=card_for(trained),
    )
    registry.set_latest(FAMILY, version)
    print(f"  wrote {FAMILY}/{version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
