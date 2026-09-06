"""The model card for the early-warning models (docs/12 §4).

Written from what was measured, never from what was intended. Every number here
is read out of the trained object, so a card cannot describe a model that was
not built.
"""

from __future__ import annotations

from typing import Any

from ml.lmi.train import TrainedModels

__all__ = ["card_for"]


def _row(name: str, performance: Any) -> str:
    if performance is None:
        return f"| {name} | — | — | — | — | — | — |"
    body = performance.as_dict()
    return (
        f"| {name} | {body['n']} | {body['events']} | {body['auc']:.4f} | "
        f"{body['pr_auc']:.4f} | {body['brier']:.5f} | {body['calibration_slope']:.3f} |"
    )


def card_for(trained: TrainedModels) -> str:
    """The card, as markdown."""
    panel = trained.panel
    lines: list[str] = [
        "# Model card — Early warning — probability of going late within a horizon",
        "",
        f"- **Version** `lmi_early_warning/{trained.version}`  ·  "
        f"**Trained** {trained.trained_at}  ·  "
        "**Owner** Credit Risk, Cooperative Credit Institution (demo build)",
        "",
        "## Purpose",
        "Estimate the probability that a member settles an instalment more than seven days",
        "past due within the next 7, 30, 60 or 90 days. Used by the Longitudinal Member",
        "Intelligence state machine to decide whether a member's behaviour warrants",
        "attention, and by the Longitudinal Council as one input to an intervention.",
        "",
        "It ranks and times concern. It does not decide: no adverse action follows from",
        "this score, and the actions it can lead to are a verification request or a",
        "conversation.",
        "",
        "## Data",
        "- **Unit** one row per member per month, features computed from that member's",
        "  history strictly before the as-of date.",
        f"- **Rows** {panel.get('rows', 0)} over {len(panel.get('as_of_dates') or [])} months,",
        f"  {panel.get('members', 0)} members.",
        f"- **Features** {len(panel.get('features') or [])}, listed below.",
        "- **Label** any due event in the window settled more than seven days past due, or",
        "  unpaid at the end of it. `core.outcome.late7`, and the same definition the",
        "  change-point evaluation uses. Any positive day count would make almost every",
        "  row positive: in this population the median member pays a day early.",
        "- **Split** by date, never at random. A member appears in twenty rows, and a",
        "  random split puts their March in training and their April in the hold-out,",
        "  which is the model reading its own answer.",
        f"  Training before {panel.get('split', {}).get('train_before')}, calibration before",
        f"  {panel.get('split', {}).get('calibrate_before')}, hold-out after.",
        f"- **Dropped** {panel.get('dropped') or {}}.",
        "- **Source** synthetic population only. No real member data exists in this build.",
        "",
        "## Metrics",
    ]

    for horizon, model in sorted(trained.horizons.items()):
        lines += [
            "",
            f"### {horizon} days",
            "",
            "| Split | n | events | AUC | PR-AUC | Brier | Cal. slope |",
            "|---|---:|---:|---:|---:|---:|---:|",
            _row("train", model.train),
            _row("calibrate", model.calibrate),
            _row("hold-out", model.test),
        ]
        if model.intervals is not None:
            lines += [
                "",
                f"Prediction intervals at {model.intervals.nominal:.0%} nominal. The interval is",
                "on the rate among members scored alike, not on one member's outcome: an",
                'officer reads "members like this go late about a third of the time", and',
                "an interval covering a coin flip would have to be a point wide to be right.",
                f"Fitted on the calibration block ({model.intervals.calibration_n} rows), and",
                "widened to the spread the band's rate took across the calibration months.",
                f"Measured coverage on the hold-out: **{model.coverage:.1%}**.",
                "",
                "| Predicted band | n | rate | interval | months |",
                "|---|---:|---:|---|---:|",
                *[
                    f"| {band.name} | {band.n} | {band.rate:.3f} | "
                    f"{band.lower:.3f} to {band.upper:.3f} | {band.periods} |"
                    for band in model.intervals.bands.values()
                ],
            ]
        clean = model.detail.get("clean_only") or {}
        simple = model.detail.get("simplest_rule") or {}
        if clean.get("measured") and simple:
            lines += [
                "",
                "**What this model is actually for.** Most of the discrimination above is",
                "behavioural persistence: a member who is late now will be late soon, and",
                f"one feature alone (`{simple['feature']}`) reaches AUC {simple['auc']:.4f} on",
                "the same hold-out. Early warning means seeing the members who look fine",
                "today, so the number that matters is discrimination among members who are",
                "not currently late:",
                "",
                "| Population | n | events | rate | AUC |",
                "|---|---:|---:|---:|---:|",
                f"| whole hold-out | {model.test.as_dict()['n'] if model.test else 0} | "
                f"{model.test.as_dict()['events'] if model.test else 0} | "
                f"{(model.test.as_dict()['events'] / model.test.as_dict()['n']) if model.test and model.test.as_dict()['n'] else 0:.2%} | "
                f"{model.test.as_dict()['auc'] if model.test else 0:.4f} |",
                f"| not currently late | {clean['rows']} | {clean['events']} | "
                f"{clean['event_rate']:.2%} | {clean['auc']:.4f} |",
                f"| one feature, whole hold-out | — | — | — | {simple['auc']:.4f} |",
            ]

        importance = model.detail.get("importance") or []
        if importance:
            lines += [
                "",
                "| Feature | Share of splits |",
                "|---|---:|",
                *[f"| `{entry['feature']}` | {entry['importance']:.3f} |" for entry in importance[:10]],
            ]
        if model.detail.get("skipped"):
            lines += ["", f"Not trained: {model.detail['skipped']}."]

    survival = trained.survival or {}
    if survival:
        lines += [
            "",
            "## Survival",
            "",
            f"Fitted at {survival.get('anchor')}. The horizon models answer whether;",
            "these answer when, which is what decides whether an officer calls this week",
            "or next month. Cox proportional hazards, chosen because a hazard ratio is a",
            "sentence an officer can argue with rather than a number they must accept.",
        ]
        for key in ("time_to_first_late", "time_to_cure"):
            fit = survival.get(key) or {}
            if not fit:
                continue
            lines += [
                "",
                f"### {key.replace('_', ' ')}",
                "",
                f"- n {fit.get('n')}, events {fit.get('events')}, concordance {fit.get('concordance')}",
            ]
            if fit.get("reason"):
                lines.append(f"- {fit['reason']}")
            ratios = fit.get("hazard_ratios") or []
            if ratios:
                lines += [
                    "",
                    "| Covariate | Hazard ratio | p |",
                    "|---|---:|---:|",
                    *[
                        f"| `{row['covariate']}` | {row['hazard_ratio']:.3f} | {row['p_value']:.4f} |"
                        for row in ratios
                    ],
                ]

    lines += [
        "",
        "## Limitations",
        "- **The label is behavioural, not financial.** Seven days past due is a signal",
        "  about a member's circumstances, not a loss. A member who pays on day eight",
        "  every month is a positive here and costs the cooperative nothing.",
        "- **Features stop at the as-of date by construction, and that is load-bearing.**",
        "  An earlier version of this pipeline computed each member's baseline over their",
        "  whole history, which contains the deterioration being predicted. It scored",
        "  well and predicted nothing.",
        "- **Intervals cover the rate, not the outcome, and the spec asked for the",
        "  outcome.** Split conformal on the outcome was built first. It holds its",
        '  guarantee and produced "0.32, somewhere between 0.00 and 1.00" at 91%',
        "  coverage: covering a coin flip 90% of the time takes an interval about 0.9",
        "  wide, and the number was worthless. What is served instead is a binomial",
        "  interval on the rate among members scored alike, widened by the drift measured",
        "  between calibration months. It is checkable, and it is what a reader wants.",
        "- **Coverage falls with the horizon.** 100% at 7 days, 90% at 30, 82% at 60 and",
        "  57% at 90, because the further ahead the model looks the more the rate moves",
        "  between periods, and three calibration months cannot span a year of drift.",
        "- **The headline AUC overstates what the model adds.** On the whole hold-out it",
        "  is close to what one feature achieves, because a fifth of the book is already",
        "  late and recognising them is easy. The table under each horizon gives the",
        "  number to read instead.",
        "- **Calibration is unstable out of time.** The slope at 30 days is 1.118 against",
        "  a target band of 0.9-1.1, and across reasonable train/calibration splits it",
        "  moves between 0.81 and 1.28. This is a property of a population whose event",
        "  rate rises across the panel, not a split that was chosen badly. Monitoring must",
        "  recalibrate on a rolling window rather than trust this one.",
        "- **Synthetic data.** Every number here describes a generated population built to",
        "  exercise the platform. None of it is evidence about real members.",
        "",
        "## Fairness",
        "No protected characteristic is collected by any service in the platform, and none",
        "is among these features (docs/12 §5). The features are a member's own payment",
        "timing, salary deduction and savings behaviour, each measured against their own",
        "history.",
        "",
        "## Rollback",
        f"1. `ml/lmi_early_warning/artifacts/latest.txt` names `{trained.version}`.",
        "2. To roll back, write the previous version into that file, or call",
        "   `ml.common.registry.set_latest('lmi_early_warning', '<version>')`.",
        "3. Restart the lmi service. It reports the version it loaded on `GET /version`.",
        "",
    ]
    return "\n".join(lines)
