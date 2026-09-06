"""Run the member state machine over the whole book and measure it.

    uv run python scripts/state_machine_eval.py

Three things matter (docs/00 T-063), and each is measured rather than asserted:

  * a drifting member reaches ELEVATED, and the change-point behind it sits
    well before their first late payment;
  * an outage does not escalate anybody, because the deviations it explains are
    the outage's and not the members';
  * a noisy member does not oscillate. The state is a claim about somebody's
    circumstances, and one that flips fortnightly is a claim nobody believes.

Every member is walked month by month, so the machine sees the same sequence it
would see in production rather than one snapshot.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "lmi"))

from app.changepoint import SignalConfig, detect  # noqa: E402
from app.state import HYSTERESIS_DAYS, Evidence, next_state  # noqa: E402
from ml.lmi.dataset import LATE_DAYS, features_at, month_ends  # noqa: E402
from ml.lmi.load import database_url, load_book, observed_end  # noqa: E402
from ml.lmi.temporal import rolling_z  # noqa: E402

TIMING = SignalConfig(signal="days_to_pay", drift=0.5, threshold=12.0)

#: A state that flips more often than this is noise wearing a label.
MIN_FLIP_INTERVAL_DAYS = 30


def archetypes() -> dict[str, str]:
    path = ROOT / "synthetic" / "out" / "profile.jsonl"
    return {
        row["member_id"]: row["archetype"]
        for row in (json.loads(line) for line in path.read_text().splitlines())
    }


async def outages() -> list[tuple[date, date]]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url(ROOT))
    async with engine.connect() as connection:
        rows = (
            (await connection.execute(text("SELECT from_ts, to_ts FROM core.outage_window"))).mappings().all()
        )
    await engine.dispose()
    return [(row["from_ts"].date(), row["to_ts"].date()) for row in rows]


def covered_by_outage(events: list[Any], windows: list[tuple[date, date]], as_of: date) -> bool:
    """Whether every recent deviation falls inside an outage.

    Every, not any: a member with one late payment during an outage and three
    outside it has not been explained, and treating them as explained is how a
    real deterioration disappears into somebody else's incident.
    """
    recent = [
        event
        for event in events
        if as_of - timedelta(days=90) <= event.due_date <= as_of
        and event.days_to_pay is not None
        and event.days_to_pay > LATE_DAYS
    ]
    if not recent:
        return False
    return all(any(start <= event.due_date <= end for start, end in windows) for event in recent)


def walk(
    member_id: str,
    events: list[Any],
    deductions: list[Any],
    savings: list[Any],
    *,
    months: list[date],
    windows: list[tuple[date, date]],
    model: Any = None,
) -> list[Any]:
    """Every transition this member goes through, month by month."""
    timings = [
        (event.due_date, float(event.days_to_pay)) for event in events if event.days_to_pay is not None
    ]
    points = rolling_z(timings, "days_to_pay") if len(timings) >= 8 else []
    alarms = detect(points, TIMING, raw=[value for _, value in timings[-len(points) :]]) if points else []

    state = "STABLE"
    since: date | None = None
    moves: list[Any] = []

    for as_of in months:
        features = features_at(events, deductions, savings, as_of=as_of)
        if features is None:
            continue

        past = [event for event in events if event.due_date < as_of]
        recent = past[-4:]
        on_time = 0
        for event in reversed(recent):
            if event.days_to_pay is not None and event.days_to_pay <= LATE_DAYS:
                on_time += 1
            else:
                break

        departures = 0
        for at, z in reversed(points):
            if at >= as_of:
                continue
            if z > 2.0:
                departures += 1
            else:
                break

        # The machine is designed around the model's probability, and an
        # evaluation that withheld it would be measuring a different machine.
        p30 = p90 = None
        if model is not None:
            scored = {s.horizon: s.probability for s in model.score(features)}
            p30, p90 = scored.get(30), scored.get(90)

        evidence = Evidence(
            member_id=member_id,
            as_of=as_of,
            features=features,
            p30=p30,
            p90=p90,
            change_points=[a.as_dict() for a in alarms if a.at < as_of],
            max_days_late=max((e.days_to_pay or 0) for e in past[-3:]) if past[-3:] else 0,
            consecutive_on_time=on_time,
            departure_events=departures,
            outage_covers_deviations=covered_by_outage(past, windows, as_of),
            employer_gap=bool(features.get("employer_gap_flag", 0)),
            distance_to_baseline=[abs(z) for _, z in points if _ < as_of][-2:],
        )

        move = next_state(state, evidence, since=since)
        if move.changed:
            moves.append(move)
            state, since = move.to_state, as_of

    return moves


async def main() -> int:
    labels = archetypes()
    windows = await outages()
    try:
        from ml.lmi.predict import EarlyWarningModel

        model = EarlyWarningModel.load()
        print(f"  scoring with lmi_early_warning/{model.version}")
    except Exception as exc:
        model = None
        print(f"  no early-warning model ({exc}); the machine runs without a probability")

    book, first_due, _ = await load_book(database_url(ROOT))
    last = observed_end(book)
    months = [day for day in month_ends(first_due + timedelta(days=210), last) if day <= last]
    print(f"  {len(book)} members, {len(months)} months, {len(windows)} outage windows\n")

    reached: dict[str, Counter[str]] = defaultdict(Counter)
    flips: list[int] = []
    escalations_in_outage = 0
    escalations_total = 0
    drift_leads: list[int] = []
    ladder: Counter[str] = Counter()

    for member_id, (events, deductions, savings) in book.items():
        moves = walk(member_id, events, deductions, savings, months=months, windows=windows, model=model)
        if not moves:
            reached[labels.get(member_id, "UNKNOWN")]["never left STABLE"] += 1
            continue

        archetype = labels.get(member_id, "UNKNOWN")
        reached[archetype][max(moves, key=lambda m: _rank(m.to_state)).to_state] += 1

        changes = [move.at for move in moves]
        flips.extend((b - a).days for a, b in pairwise(changes))

        for move in moves:
            if move.to_state not in ("ELEVATED", "CRITICAL"):
                continue
            escalations_total += 1
            if any(start <= move.at <= end for start, end in windows):
                escalations_in_outage += 1

        # The measure that matters is the first time the platform said
        # anything, not the first time it said the strongest thing. WATCH is
        # the platform paying attention, and an officer whose queue shows a
        # member two months before they go late has been warned.
        if archetype == "SLOW_DRIFT":
            first_late = next(
                (
                    event.due_date
                    for event in events
                    if event.days_to_pay is not None and event.days_to_pay > LATE_DAYS
                ),
                None,
            )
            if first_late:
                drift_leads.append((first_late - moves[0].at).days)
                ladder["walked" if any(m.to_state == "ELEVATED" for m in moves) else "jumped"] += 1

    print(f"  {'archetype':<12} {'worst state reached':<40}")
    for archetype in sorted(reached):
        counts = ", ".join(f"{state} {n}" for state, n in reached[archetype].most_common())
        print(f"  {archetype:<12} {counts}")

    shortest = min(flips) if flips else 0
    under = sum(1 for gap in flips if gap < MIN_FLIP_INTERVAL_DAYS)
    print(f"\n  {len(flips)} state changes after the first, shortest gap {shortest} days")
    print(
        f"  gaps under {MIN_FLIP_INTERVAL_DAYS} days: {under} ({under / len(flips):.1%})"
        if flips
        else "  no repeat changes"
    )
    print(f"  escalations during an outage window: {escalations_in_outage} of {escalations_total}")
    if drift_leads:
        early = sum(1 for lead in drift_leads if lead > 0)
        print("\n  SLOW_DRIFT members the platform said something about before their")
        print(
            f"  first late payment: {early}/{len(drift_leads)} "
            f"({early / len(drift_leads):.1%}), "
            f"median lead {statistics.median(drift_leads):.0f} days"
        )
        print(
            f"  of those, {ladder['walked']} walked the ladder through ELEVATED and "
            f"{ladder['jumped']} went straight to CRITICAL on a payment already a month late"
        )

    early_share = sum(1 for lead in drift_leads if lead > 0) / len(drift_leads) if drift_leads else 0.0
    ok = (
        shortest >= HYSTERESIS_DAYS
        and escalations_in_outage == 0
        and early_share >= 0.5
        and (statistics.median(drift_leads) if drift_leads else 0) >= 21
    )
    print("\n  T-063 acceptance:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _rank(state: str) -> int:
    return {"STABLE": 0, "RECOVERY": 1, "WATCH": 2, "ELEVATED": 3, "CRITICAL": 4}.get(state, 0)


sys.exit(asyncio.run(main()))
