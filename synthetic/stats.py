"""Portfolio statistics and the sanity ranges from docs/10 §4.6.

The ranges are the contract for the generated population. `synthetic.cli stats`
exits non-zero when the population drifts outside them.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from synthetic.config import ARCHETYPE_MIX
from synthetic.population.behaviour import LATE_THRESHOLD_DAYS, MISSED_THRESHOLD_DAYS
from synthetic.writer import DEFAULT_OUT, read_table

__all__ = ["RANGES", "summarise"]

#: docs/10 §4.6 — what a healthy generated population looks like.
RANGES: dict[str, tuple[float, float]] = {
    "delinquency_30d_rate": (0.06, 0.08),
    "steady_late30_rate": (0.0, 0.01),
    "chronic_late30_rate": (0.25, 1.0),
    "slow_drift_first_late_in_window": (0.80, 1.0),
    "deduction_missed_rate": (0.01, 0.06),
    "savings_paused_share": (0.08, 0.20),
}


def _days_late(due: str, paid_at: str | None) -> float | None:
    if not paid_at:
        return None
    paid = datetime.fromisoformat(paid_at.replace("Z", "+00:00")).date()
    return (paid - date.fromisoformat(due)).days


def summarise(out: Path = DEFAULT_OUT) -> dict[str, Any]:
    """Measure the population and compare it with the documented ranges."""
    schedules = read_table("schedule", out)
    payments = read_table("payment", out)
    deductions = read_table("deduction", out)
    profiles = read_table("profile", out)
    accounts = read_table("account", out)

    paid_by_schedule = {p["schedule_id"]: p["paid_at"] for p in payments}
    archetype_of_account = {}
    archetype_of_member = {p["member_id"]: p["archetype"] for p in profiles}
    for account in accounts:
        archetype_of_account[account["account_id"]] = archetype_of_member.get(account["member_id"], "STEADY")

    total = 0
    late30 = 0
    by_archetype: dict[str, list[int]] = defaultdict(list)

    for row in schedules:
        days = _days_late(row["due_date"], paid_by_schedule.get(row["schedule_id"]))
        # an unpaid due event is delinquent by definition
        is_late30 = days is None or days > MISSED_THRESHOLD_DAYS
        total += 1
        late30 += int(is_late30)
        by_archetype[archetype_of_account[row["account_id"]]].append(int(is_late30))

    # Only members with fourteen months of history before the drift can show
    # the S8 pattern; an account opened in month 22 has no clean run to drift
    # away from (docs/10 §4, docs/11 S8).
    drift = [
        p
        for p in profiles
        if p["archetype"] == "SLOW_DRIFT" and (p.get("earliest_account_month") or 99) <= 14
    ]
    in_window = [
        p for p in drift if p.get("first_late_month") is not None and 18 <= p["first_late_month"] <= 21
    ]

    measured = {
        "delinquency_30d_rate": round(late30 / total, 4) if total else 0.0,
        "steady_late30_rate": round(sum(by_archetype["STEADY"]) / len(by_archetype["STEADY"]), 4)
        if by_archetype["STEADY"]
        else 0.0,
        "chronic_late30_rate": round(sum(by_archetype["CHRONIC"]) / len(by_archetype["CHRONIC"]), 4)
        if by_archetype["CHRONIC"]
        else 0.0,
        "slow_drift_first_late_in_window": round(len(in_window) / len(drift), 4) if drift else 0.0,
        "deduction_missed_rate": round(
            sum(1 for d in deductions if d["received_amount"] is None) / len(deductions), 4
        )
        if deductions
        else 0.0,
        "savings_paused_share": round(
            sum(1 for p in profiles if p["savings_paused_months"] > 0) / len(profiles), 4
        )
        if profiles
        else 0.0,
    }

    checks = {
        name: {
            "value": measured[name],
            "range": list(RANGES[name]),
            "ok": RANGES[name][0] <= measured[name] <= RANGES[name][1],
        }
        for name in RANGES
    }

    archetype_mix: dict[str, int] = defaultdict(int)
    for profile in profiles:
        archetype_mix[profile["archetype"]] += 1

    from synthetic.labels import label_accounts, summarise_labels

    labels = summarise_labels(label_accounts(schedules, payments, read_table("arrangement", out)))

    return {
        "members": len(profiles),
        "labels": labels,
        "accounts": len(accounts),
        "due_events": total,
        "late_threshold_days": LATE_THRESHOLD_DAYS,
        "archetype_mix": {k: round(v / len(profiles), 4) for k, v in sorted(archetype_mix.items())}
        if profiles
        else {},
        "archetype_target": dict(sorted(ARCHETYPE_MIX.items())),
        "checks": checks,
        "within_ranges": all(c["ok"] for c in checks.values()),
    }
