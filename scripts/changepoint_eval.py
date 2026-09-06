"""How early the change-point detector sees a drift, and how often it is wrong.

    uv run python scripts/changepoint_eval.py

Runs the detector over every member's real payment history and scores it
against the archetype the generator used, which the platform never sees. Two
numbers matter (docs/00 T-061):

  * median lead on SLOW_DRIFT members, measured from the change-point to their
    first late payment. Target 21 days or more.
  * false alarms on STEADY members, as a rate per member-year. Target 1.5% or
    fewer.

"Late" is the platform's own first rung, more than seven days past due, not
merely after the due date. In this population the median member pays one day
early and half of them settle a day or two on, so counting any positive
day-count as late puts the first late event at a member's second instalment
and makes early warning impossible by definition.

The members are split in half by id. Parameters are chosen on one half and the
numbers reported on the other, because a detector tuned until the numbers look
right on the set it was tuned on has learnt the set.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "lmi"))

from app.changepoint import SignalConfig, detect  # noqa: E402
from ml.lmi.temporal import rolling_z  # noqa: E402

#: docs/07 §4.3 — more than seven days past due, matching `core.outcome.late7`.
LATE_DAYS = 7

#: The grid searched on the tuning half. The spec's k=0.5, h=4.0 is in it, and
#: is what a signal without its own entry in the config still gets.
GRID = [
    SignalConfig(signal="days_to_pay", drift=drift, threshold=threshold)
    for drift in (0.5, 0.75, 1.0, 1.25, 1.5)
    for threshold in (4.0, 6.0, 8.0, 10.0, 12.0)
]


def archetypes() -> dict[str, str]:
    path = ROOT / "synthetic" / "out" / "profile.jsonl"
    if not path.is_file():
        raise SystemExit(f"  no generated profiles at {path}")
    return {
        row["member_id"]: row["archetype"]
        for row in (json.loads(line) for line in path.read_text().splitlines())
    }


async def histories() -> dict[str, list[tuple[date, int]]]:
    """Every member's settled due events, oldest first."""
    import os

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    values: dict[str, str] = {}
    env = ROOT / "docker" / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()

    url = os.environ.get("DATABASE_URL") or (
        f"postgresql+asyncpg://{values.get('POSTGRES_USER', 'cio')}:"
        f"{values.get('POSTGRES_PASSWORD', '')}@localhost:"
        f"{values.get('POSTGRES_PORT', '5432')}/{values.get('POSTGRES_DB', 'cio')}"
    )

    engine = create_async_engine(url)
    out: dict[str, list[tuple[date, int]]] = defaultdict(list)
    async with engine.connect() as connection:
        rows = await connection.stream(
            text("""
        SELECT a.member_id, s.due_date,
               min(p.paid_at) FILTER (WHERE NOT p.reversed) AS paid_at
          FROM core.schedule s
          JOIN core.account a ON a.account_id = s.account_id
          LEFT JOIN core.payment p ON p.schedule_id = s.schedule_id
         GROUP BY a.member_id, s.schedule_id, s.due_date
         ORDER BY a.member_id, s.due_date
    """)
        )
        async for row in rows.mappings():
            if row["paid_at"] is None:
                continue
            out[row["member_id"]].append((row["due_date"], (row["paid_at"].date() - row["due_date"]).days))
    await engine.dispose()
    return dict(out)


def score(events: list[tuple[date, int]], config: SignalConfig) -> list[Any]:
    """Detect on the robust-z series of this member's own payment timing."""
    if len(events) < 8:
        return []
    timings = [float(timing) for _, timing in events]
    # Causal: each payment is judged against the payments before it, never
    # against a baseline that already contains the drift being looked for.
    points = rolling_z([(at, float(timing)) for at, timing in events], "days_to_pay")
    if not points:
        return []
    return detect(points, config, raw=timings[-len(points) :])


def first_late(events: list[tuple[date, int]]) -> date | None:
    """The first materially late settlement, not the first positive day count."""
    for at, timing in events:
        if timing > LATE_DAYS:
            return at
    return None


def tuning_half(member_id: str) -> bool:
    """A stable split by id, so the two halves do not move between runs."""
    return sum(ord(character) for character in member_id) % 2 == 0


def measure(
    behaviour: dict[str, list[tuple[date, int]]],
    labels: dict[str, str],
    config: SignalConfig,
    *,
    members: set[str],
) -> dict[str, Any]:
    """Lead time and false alarms for one parameter choice."""
    leads: list[int] = []
    detected = 0
    drift_members = 0
    steady_alarms = 0
    steady_years = 0.0

    for member_id in members:
        events = behaviour[member_id]
        archetype = labels.get(member_id, "UNKNOWN")
        alarms = score(events, config)

        if archetype == "SLOW_DRIFT":
            drift_members += 1
            late = first_late(events)
            if late is not None:
                before = [alarm for alarm in alarms if alarm.at <= late]
                if before:
                    detected += 1
                    leads.append((late - before[0].at).days)

        if archetype == "STEADY":
            steady_alarms += len(alarms)
            span = (events[-1][0] - events[0][0]).days
            steady_years += max(span, 1) / 365.0

    return {
        "median_lead": statistics.median(leads) if leads else 0,
        "recall": detected / drift_members if drift_members else 0.0,
        "false_rate": steady_alarms / steady_years if steady_years else 0.0,
        "drift_members": drift_members,
        "detected": detected,
        "steady_alarms": steady_alarms,
        "steady_years": steady_years,
    }


async def main() -> int:
    labels = archetypes()
    behaviour = await histories()
    usable = {member for member, events in behaviour.items() if len(events) >= 8}
    tuning = {member for member in usable if tuning_half(member)}
    holdout = usable - tuning
    print(f"  {len(behaviour)} members with payment history; {len(tuning)} tuning, {len(holdout)} held out\n")

    # --- choose parameters on the tuning half ------------------------------
    print(f"  {'drift':>6} {'threshold':>10} {'lead':>6} {'recall':>8} {'false/yr':>9}")
    best: tuple[float, SignalConfig] | None = None
    for config in GRID:
        scored = measure(behaviour, labels, config, members=tuning)
        print(
            f"  {config.drift:>6.2f} {config.threshold:>10.1f} "
            f"{scored['median_lead']:>6.0f} {scored['recall']:>7.1%} "
            f"{scored['false_rate']:>8.2%}"
        )
        if scored["false_rate"] > 0.015 or scored["median_lead"] < 21:
            continue
        # Among the configurations that meet both targets, the one that finds
        # the most drifting members. Recall is what is left to maximise once
        # the two constraints are satisfied.
        if best is None or scored["recall"] > best[0]:
            best = (scored["recall"], config)

    if best is None:
        print("\n  no configuration met both targets on the tuning half")
        return 1

    chosen = best[1]
    print(f"\n  chosen on the tuning half: drift {chosen.drift}, threshold {chosen.threshold}")

    # --- report on the held-out half ---------------------------------------
    final = measure(behaviour, labels, chosen, members=holdout)
    by_archetype: dict[str, dict[str, int]] = defaultdict(lambda: {"members": 0, "alarmed": 0})
    for member_id in holdout:
        bucket = by_archetype[labels.get(member_id, "UNKNOWN")]
        bucket["members"] += 1
        if score(behaviour[member_id], chosen):
            bucket["alarmed"] += 1

    print(f"\n  held out ({len(holdout)} members)")
    print(f"  {'archetype':<12} {'members':>8} {'alarmed':>8} {'share':>8}")
    for archetype in sorted(by_archetype):
        row = by_archetype[archetype]
        share = row["alarmed"] / row["members"] if row["members"] else 0.0
        print(f"  {archetype:<12} {row['members']:>8} {row['alarmed']:>8} {share:>7.1%}")

    print(
        f"\n  SLOW_DRIFT seen before the first late payment: "
        f"{final['detected']}/{final['drift_members']} ({final['recall']:.1%})"
    )
    print(f"  median lead: {final['median_lead']:.0f} days (target >= 21)")
    print(
        f"  STEADY false alarms: {final['steady_alarms']} over "
        f"{final['steady_years']:.0f} member-years = {final['false_rate']:.2%} per year "
        f"(target <= 1.5%)"
    )

    ok = final["median_lead"] >= 21 and final["false_rate"] <= 0.015
    print("\n  T-061 acceptance:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
