"""Turning a member's history into temporal features (docs/07 §4.2).

Reads the core through SQL rather than through the core stub's HTTP API. The
nightly run touches five thousand members and would otherwise make about twenty
thousand HTTP calls; the same reduction over the same rows in one query per
family finishes in seconds. The stub's API remains the interface for anything
reading one member.

Nothing here decides anything. It computes what a member's behaviour looked
like on a day, and the change-point detector and the state machine read it.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cio_common.hashing import canonical_json, sha256
from cio_common.ids import new_id
from ml.lmi.families import (
    Deduction,
    Interaction,
    SavingsPoint,
    baselines_for,
    capacity_features,
    deduction_features,
    departures,
    interaction_features,
    savings_features,
)
from ml.lmi.seasonal import adjust
from ml.lmi.temporal import DueEvent, days_to_pay, late_streak, window_features

__all__ = ["Materialisation", "MemberHistory", "compute_for", "load_histories", "materialise"]

#: How far back a materialisation reads. A year of history is what the
#: baselines need; two years is what the seasonal adjustment needs, and it is
#: cheap enough to read once.
HISTORY_DAYS = 760


@dataclass
class MemberHistory:
    """Everything one member did, in the window that matters."""

    member_id: str
    due_events: list[DueEvent] = field(default_factory=list)
    deductions: list[Deduction] = field(default_factory=list)
    savings: list[SavingsPoint] = field(default_factory=list)
    interactions: list[Interaction] = field(default_factory=list)
    dsr_history: list[tuple[date, float]] = field(default_factory=list)
    new_obligations_6m: int = 0


@dataclass
class Materialisation:
    """What one run did."""

    run_id: str
    as_of: date
    members: int = 0
    computed: int = 0
    skipped: int = 0
    failed: int = 0
    seconds: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "as_of": self.as_of.isoformat(),
            "members": self.members,
            "computed": self.computed,
            "skipped": self.skipped,
            "failed": self.failed,
            "seconds": round(self.seconds, 2),
            "detail": self.detail,
        }


async def load_histories(
    db: AsyncSession, *, as_of: date, member_ids: list[str] | None = None
) -> dict[str, MemberHistory]:
    """Read every member's behaviour in four queries, not four per member."""
    since = as_of - timedelta(days=HISTORY_DAYS)
    # The member filter is a nullable array parameter rather than an
    # interpolated clause, so there is one query text whatever the scope is and
    # no string is ever built around a value.
    scope = {"since": since, "as_of": as_of, "members": member_ids}

    histories: dict[str, MemberHistory] = defaultdict(lambda: MemberHistory(member_id=""))

    def history(member_id: str) -> MemberHistory:
        found = histories[member_id]
        if not found.member_id:
            found.member_id = member_id
        return found

    # --- due events and what was paid against them -------------------------
    rows = (
        (
            await db.execute(
                text("""
        SELECT a.member_id, s.due_date, s.amount_due,
               min(p.paid_at) FILTER (WHERE NOT p.reversed) AS paid_at,
               coalesce(sum(p.amount_paid) FILTER (WHERE NOT p.reversed), 0) AS amount_paid
          FROM core.schedule s
          JOIN core.account a ON a.account_id = s.account_id
          LEFT JOIN core.payment p ON p.schedule_id = s.schedule_id
         WHERE s.due_date BETWEEN :since AND :as_of
           AND (CAST(:members AS text[]) IS NULL
                OR a.member_id = ANY(CAST(:members AS text[])))
         GROUP BY a.member_id, s.schedule_id, s.due_date, s.amount_due
         ORDER BY a.member_id, s.due_date
    """),
                scope,
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        paid_at = row["paid_at"]
        history(row["member_id"]).due_events.append(
            DueEvent(
                due_date=row["due_date"],
                amount_due=float(row["amount_due"]),
                paid_at=paid_at.date() if paid_at else None,
                amount_paid=float(row["amount_paid"]),
            )
        )

    # --- salary deductions --------------------------------------------------
    # `cycle` is a 'YYYY-MM' label, not a date, so it is parsed to the first
    # of the month here rather than compared as text: '2024-9' would sort
    # after '2024-10' and the window would be wrong.
    rows = (
        (
            await db.execute(
                text("""
        SELECT member_id, cycle, expected_amount, received_amount, employer_id
          FROM core.deduction
         WHERE to_date(cycle, 'YYYY-MM') BETWEEN :since AND :as_of
           AND (CAST(:members AS text[]) IS NULL
                OR member_id = ANY(CAST(:members AS text[])))
         ORDER BY member_id, cycle
    """),
                scope,
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        year, month = (int(part) for part in str(row["cycle"]).split("-"))
        history(row["member_id"]).deductions.append(
            Deduction(
                cycle=date(year, month, 1),
                expected=float(row["expected_amount"] or 0),
                actual=float(row["received_amount"] or 0),
                employer_id=row["employer_id"] or "",
            )
        )

    # --- savings ------------------------------------------------------------
    # The core records a balance, not a contribution. What went in is the
    # difference from the member's previous point, and a withdrawal is a
    # contribution of zero rather than a negative one: pausing and drawing
    # down are the same signal for "stopped putting money aside".
    rows = (
        (
            await db.execute(
                text("""
        SELECT member_id, as_of AS at, balance,
               balance - lag(balance) OVER (PARTITION BY member_id ORDER BY as_of) AS delta
          FROM core.savings
         WHERE as_of BETWEEN :since AND :as_of
           AND (CAST(:members AS text[]) IS NULL
                OR member_id = ANY(CAST(:members AS text[])))
         ORDER BY member_id, as_of
    """),
                scope,
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        history(row["member_id"]).savings.append(
            SavingsPoint(
                balance=float(row["balance"]),
                at=row["at"],
                contribution=max(float(row["delta"] or 0), 0.0),
            )
        )

    # Interactions are deliberately absent. The core carries no contact
    # history, so the interaction family computes over an empty list and
    # reports zeros that are true: nobody has contacted these members. Filling
    # it with plausible contacts would put a number on the screen that no
    # record supports.

    return {member_id: found for member_id, found in histories.items() if member_id}


def compute_for(
    history: MemberHistory, *, as_of: date, employer_cycles: dict[str, dict[date, float]]
) -> dict[str, Any]:
    """One member's feature set, with its baselines and its season."""
    features: dict[str, float] = {}
    features.update(window_features(history.due_events, as_of=as_of))
    features.update(
        deduction_features(
            history.deductions,
            as_of=as_of,
            employer_cycles=employer_cycles.get(
                history.deductions[-1].employer_id if history.deductions else "", {}
            ),
        )
    )
    features.update(savings_features(history.savings, as_of=as_of))
    features.update(interaction_features(history.interactions, as_of=as_of))
    features.update(
        capacity_features(
            dsr_history=history.dsr_history,
            as_of=as_of,
            new_obligations_6m=history.new_obligations_6m,
        )
    )

    # The baselines are the member's own history on the signals that have one.
    timings = [float(value) for value in days_to_pay(history.due_events)]
    baselines = baselines_for(
        {
            "days_to_pay_median_30d": timings,
            "days_to_pay_median_90d": timings,
            "deduction_amount_delta_90d": [d.shortfall for d in history.deductions],
        }
    )
    features.update(departures(features, baselines))

    # `late_streak` counts due events settled after the due date, which in this
    # book is 42% of members: paying two or three days on is a habit here, not
    # a deterioration. The streak that matters for an alert is the one measured
    # against the member's own habit, so both are reported.
    #
    # It is not called a late streak, because for a member who habitually pays
    # three days early, paying on the due date is beyond habit and is not late.
    # That is a real signal and a different one.
    habit = baselines["days_to_pay_median_30d"]
    features["streak_beyond_habit"] = float(
        late_streak(
            history.due_events,
            tolerance_days=round(habit.median) if habit.usable else 0,
        )
    )

    seasonal = adjust(
        [
            (event.due_date, float(event.days_to_pay))
            for event in history.due_events
            if event.days_to_pay is not None
        ]
    )

    return {
        "features": features,
        "baselines": {name: baseline.as_dict() for name, baseline in baselines.items()},
        "seasonal": seasonal.as_dict(),
        "due_events": len(history.due_events),
    }


async def employer_cycle_gaps(db: AsyncSession, *, as_of: date) -> dict[str, dict[date, float]]:
    """The share of each employer's members who missed each cycle.

    Computed once for the whole run rather than per member: it is the same
    question asked five thousand times, and asking it once is what makes an
    employer-wide gap distinguishable from a member's own.
    """
    since = as_of - timedelta(days=HISTORY_DAYS)
    rows = (
        (
            await db.execute(
                text("""
        SELECT employer_id, cycle,
               avg(CASE WHEN coalesce(received_amount, 0) <= 0 THEN 1.0 ELSE 0.0 END)
                 AS missed_share
          FROM core.deduction
         WHERE to_date(cycle, 'YYYY-MM') BETWEEN :since AND :as_of
           AND employer_id IS NOT NULL
         GROUP BY employer_id, cycle
    """),
                {"since": since, "as_of": as_of},
            )
        )
        .mappings()
        .all()
    )
    gaps: dict[str, dict[date, float]] = defaultdict(dict)
    for row in rows:
        year, month = (int(part) for part in str(row["cycle"]).split("-"))
        gaps[row["employer_id"]][date(year, month, 1)] = float(row["missed_share"])
    return dict(gaps)


async def materialise(
    db: AsyncSession, *, as_of: date, member_ids: list[str] | None = None
) -> Materialisation:
    """Compute and store a day's features for every member in scope."""
    started = time.perf_counter()
    run = Materialisation(run_id=new_id("fs"), as_of=as_of)

    histories = await load_histories(db, as_of=as_of, member_ids=member_ids)
    gaps = await employer_cycle_gaps(db, as_of=as_of)
    run.members = len(histories)

    for member_id, history in histories.items():
        if not history.due_events and not history.deductions:
            # Nothing happened to this member in the window. Storing an empty
            # feature set would let a member with no facility look identical to
            # one whose behaviour was measured and found unremarkable.
            run.skipped += 1
            continue

        computed = compute_for(history, as_of=as_of, employer_cycles=gaps)
        digest = sha256(canonical_json(computed["features"]))
        await db.execute(
            text("""
            INSERT INTO app_lmi.temporal_features
              (member_id, as_of, features, baselines, seasonal, due_events, digest)
            VALUES (:member_id, :as_of, CAST(:features AS jsonb), CAST(:baselines AS jsonb),
                    CAST(:seasonal AS jsonb), :due_events, :digest)
            ON CONFLICT (member_id, as_of) DO UPDATE SET
              features = EXCLUDED.features, baselines = EXCLUDED.baselines,
              seasonal = EXCLUDED.seasonal, due_events = EXCLUDED.due_events,
              digest = EXCLUDED.digest, computed_at = now()
        """),
            {
                "member_id": member_id,
                "as_of": as_of,
                "features": canonical_json(computed["features"]).decode(),
                "baselines": canonical_json(computed["baselines"]).decode(),
                "seasonal": canonical_json(computed["seasonal"]).decode(),
                "due_events": computed["due_events"],
                "digest": digest,
            },
        )
        run.computed += 1

    run.seconds = time.perf_counter() - started
    await db.execute(
        text("""
        INSERT INTO app_lmi.materialisation
          (run_id, as_of, members, computed, skipped, failed, seconds, finished_at)
        VALUES (:run_id, :as_of, :members, :computed, :skipped, :failed, :seconds, now())
    """),
        # From the dataclass, not from as_dict(): that renders `as_of` as an
        # ISO string for a caller, and asyncpg binds a date column to a date.
        {
            "run_id": run.run_id,
            "as_of": run.as_of,
            "members": run.members,
            "computed": run.computed,
            "skipped": run.skipped,
            "failed": run.failed,
            "seconds": run.seconds,
        },
    )
    return run
