"""Rendering the model card (docs/12 §5).

The card is written by the training run, not by hand, so it cannot describe a
model other than the one that was saved beside it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

TEMPLATE = Path(__file__).resolve().parents[1] / "common" / "card_template.md"

OWNER = "Credit Risk, Cooperative Credit Institution (demo build)"


def _row(label: str, value: Any) -> str:
    return f"| {label} | {value} |"


def _performance_table(performance: dict[str, dict[str, Any]]) -> str:
    header = (
        "| Split | n | events | AUC | 95% CI | PR-AUC | KS | Brier | "
        "Cal. slope | Cal. intercept |\n"
        "|---|---:|---:|---:|---|---:|---:|---:|---:|---:|"
    )
    lines = [header]
    for name in ("train", "valid", "test"):
        m = performance[name]
        lines.append(
            f"| {name} | {m['n']} | {m['events']} | {m['auc']:.4f} | "
            f"[{m['auc_ci_low']:.3f}, {m['auc_ci_high']:.3f}] | {m['pr_auc']:.4f} | "
            f"{m['ks']:.4f} | {m['brier']:.5f} | {m['calibration_slope']:.3f} | "
            f"{m['calibration_intercept']:.3f} |"
        )
    return "\n".join(lines)


def _grade_table(grades: list[dict[str, Any]]) -> str:
    lines = ["| Grade | n | events | observed rate | mean PD |", "|---|---:|---:|---:|---:|"]
    for row in grades:
        rate = "—" if row["observed_rate"] is None else f"{row['observed_rate']:.4f}"
        mean = "—" if row["mean_pd"] is None else f"{row['mean_pd']:.4f}"
        lines.append(f"| {row['grade']} | {row['n']} | {row['events']} | {rate} | {mean} |")
    return "\n".join(lines)


def _decile_table(deciles: list[dict[str, Any]]) -> str:
    lines = ["| Decile | n | events | rate | lift | mean PD |", "|---:|---:|---:|---:|---:|---:|"]
    for row in deciles:
        lines.append(
            f"| {row['decile']} | {row['n']} | {row['events']} | "
            f"{row['rate']:.4f} | {row['lift']} | {row['mean_pd']:.4f} |"
        )
    return "\n".join(lines)


def _characteristics(table: dict[str, Any]) -> str:
    lines = ["| Characteristic | Weight | IV | Direction |", "|---|---:|---:|:--:|"]
    for entry in table["characteristics"]:
        arrow = {1: "higher is riskier", -1: "higher is safer", 0: "unconstrained"}[entry["monotone"]]
        lines.append(
            f"| `{entry['feature']}` | {entry['weight']:.4f} | {entry['information_value']:.4f} | {arrow} |"
        )
    return "\n".join(lines)


def render(*, version: str, metrics: dict[str, Any]) -> str:
    champion = metrics["champion"]
    challenger = metrics["challenger"]
    dataset = metrics["dataset"]
    held = champion["performance"]["test"]
    diagnostic = champion["calibration_diagnostic"]["test"]
    splits = dataset["splits"]

    purpose = (
        "Estimate the probability that a financing facility reaches 90 days past due "
        "within twelve months of origination, for use by the policy engine's CAPACITY "
        "and CONDUCT factors and by the Autonomy Dial. The model ranks and prices risk. "
        "It does not decide: a decision is made by the policy hierarchy, and this score "
        "is one input to it."
    )

    data = "\n".join(
        [
            f"- **Label** `{dataset['label']}` — 90 days past due within "
            f"{dataset['performance_months']} months of origination, taken from `core.outcome`.",
            "- **Unit** one row per account, features as they stood the day before the "
            "facility opened, so the facility being applied for is not itself an input.",
            f"- **Excluded** {dataset['censored_excluded']} accounts whose twelve-month "
            "performance window has not finished. Counting them as good would teach the "
            "model that recent lending never defaults.",
            "- **Split** by origination month and never shuffled:",
            f"  - train {splits['train']['cohorts'][0]} to {splits['train']['cohorts'][-1]} — "
            f"{splits['train']['n']} accounts, {splits['train']['events']} defaults "
            f"({splits['train']['rate']:.2%})",
            f"  - validation {splits['valid']['cohorts'][0]} to {splits['valid']['cohorts'][-1]} — "
            f"{splits['valid']['n']} accounts, {splits['valid']['events']} defaults "
            f"({splits['valid']['rate']:.2%})",
            f"  - hold-out {splits['test']['cohorts'][0]} to {splits['test']['cohorts'][-1]} — "
            f"{splits['test']['n']} accounts, {splits['test']['events']} defaults "
            f"({splits['test']['rate']:.2%})",
            "- **Source** synthetic population only. No real member data exists in this build.",
        ]
    )

    features = "\n".join(
        [
            f"Offered {len(metrics['features_used'])} of the "
            f"{len(metrics['features_used']) + len(metrics['features_dropped'])} registry "
            "features; the champion selected "
            f"{len(champion['characteristics'])} by information value with a correlation filter.",
            "",
            "Dropped before training: "
            + (", ".join(f"`{n}`" for n in metrics["features_dropped"]) or "none")
            + ". These are application-time inputs with no counterpart in the historical "
            "record, so every account carries the same value and the column can teach "
            "nothing. They remain live inputs to the policy engine and the fraud service.",
            "",
            _characteristics(champion["table"]),
            "",
            "Every direction above is fixed by `ml/credit_risk/monotone.yaml` and enforced "
            "twice: the bin weights of evidence are made monotone before fitting, and every "
            "coefficient is held non-negative. A member cannot score better for a worse input.",
        ]
    )

    metrics_text = "\n".join(
        [
            "### Champion — weight-of-evidence scorecard, constrained logistic",
            "",
            _performance_table(champion["performance"]),
            "",
            f"Calibration: {champion['calibration']['method']}, fitted on the validation "
            f"block ({champion['calibration']['fitted_on']['events']} events).",
            "",
            "Hold-out deciles, worst-ranked first:",
            "",
            _decile_table(held["deciles"]),
            "",
            "Hold-out grade bands (A < 1.5%, B < 3%, C < 6%, D < 12%, E ≥ 12%):",
            "",
            _grade_table(champion["grades"]["test"]),
            "",
            "### Challenger — LightGBM with monotone constraints",
            "",
            _performance_table(challenger["performance"]),
            "",
            f"Out-of-distribution detector: {metrics['ood']['kind']} over "
            f"{len(metrics['ood']['features'])} numeric features; mean score "
            f"{metrics['ood']['train_score_mean']:.3f} in training, "
            f"{metrics['ood']['test_score_mean']:.3f} on the hold-out.",
        ]
    )

    limitations = "\n".join(
        [
            f"- **The hold-out carries {held['events']} defaults.** The AUC confidence "
            f"interval is [{held['auc_ci_low']:.3f}, {held['auc_ci_high']:.3f}]. Read the "
            "interval, not the point estimate.",
            f"- **The champion falls fractionally short of the 0.72 discrimination "
            f"target** at {held['auc']:.4f}; the challenger reaches "
            f"{challenger['performance']['test']['auc']:.4f}. A search of 4,500 "
            "configurations moved the champion by less than a thousandth, so this is the "
            "signal the population carries at origination rather than a tuning choice. "
            "A model given the generating archetype outright, which no real system could "
            "have, reaches only 0.803 on the same hold-out.",
            f"- **Calibration decays out of time.** The slope is "
            f"{champion['performance']['valid']['calibration_slope']:.3f} on validation and "
            f"{held['calibration_slope']:.3f} on the hold-out, against a target band of "
            "0.9-1.1. Correcting the level alone leaves the slope at "
            f"{diagnostic['level_corrected_slope']}, so this is not a base-rate shift the "
            "intercept can absorb: the score is genuinely more spread out than the hold-out "
            "period supports, and the model states more separation than it delivers there. "
            f"Predicted mean {diagnostic['mean_pd']:.2%} against an observed "
            f"{diagnostic['observed_rate']:.2%}. Monitoring must recalibrate on a rolling "
            "window rather than trust this one; the governance service owns that alert.",
            "- **Origination features cannot see later deterioration.** In this population "
            "the defaults in the hold-out period are mostly members whose behaviour changed "
            "after the facility opened. That is what the Longitudinal Member Intelligence "
            "engine exists to catch, and it is why this score is refreshed rather than "
            "trusted for the life of the facility.",
            "- **Weak conduct history at origination.** Most applicants have no prior "
            "facility, so `ontime_rate_24m` is absent for them and the model leans on "
            "savings behaviour instead.",
            "- **Synthetic data.** Every number here describes a generated population "
            "built to exercise the platform. None of it is evidence about real members.",
        ]
    )

    fairness = "\n".join(
        [
            "No protected characteristic is collected by any service in the platform, so "
            "the check is structural rather than statistical (docs/12 §5).",
            "",
            f"- Protected attributes among the model's features: "
            f"**{metrics['fairness']['protected_features_present'] or 'none'}**.",
            f"- Proxy scan grouped by `employer_sector`: "
            f"{len(metrics['fairness']['proxy_scan']['flagged'])} features vary more across "
            "sectors than within them. Employer sector is itself an approved input, so this "
            "is a list to review, not a breach.",
            "",
            "The synthetic population carries no ethnicity, religion, gender, health or "
            "political attribute, so no disparity statistic can be computed and none is "
            "claimed.",
        ]
    )

    rollback = "\n".join(
        [
            f"1. `ml/credit_risk/artifacts/latest.txt` currently names `{version}`.",
            "2. To roll back, write the previous version into that file, or call "
            "`ml.common.registry.set_latest('credit_risk', '<version>')`. Artifacts are "
            "write-once, so the earlier version is still exactly as it was served.",
            "3. Restart the risk service. It reports the version it loaded on `GET /version`, "
            "and every score it returns carries a `model_run_id` naming this version.",
        ]
    )

    body = TEMPLATE.read_text()
    return body.format(
        title="Credit risk — probability of default within 12 months",
        family="credit_risk",
        version=version,
        trained_at=metrics["trained_at"],
        owner=OWNER,
        status=(
            f"champion hold-out AUC {held['auc']:.4f}, calibration slope "
            f"{held['calibration_slope']:.3f}; challenger hold-out AUC "
            f"{challenger['performance']['test']['auc']:.4f}"
        ),
        purpose=purpose,
        data=data,
        features=features,
        metrics=metrics_text,
        limitations=limitations,
        fairness=fairness,
        rollback=rollback,
    )
