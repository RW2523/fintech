"""Running change-point detection over a member's history (docs/07 §4.3).

Sits between the materialised features and the state machine. Reads what a
member actually did, runs each configured signal through CUSUM and PELT, and
emits `behaviour.change_point_detected` for what it finds.

Nothing here decides what to do about a change. The state machine does that,
and keeping the two apart is what lets a detection be reviewed without arguing
about the action it triggered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.changepoint import Alarm
from app.changepoint import detect as run_detect
from app.config import LmiConfig, load_config
from app.materialise import MemberHistory, load_histories
from cio_common.outbox import emit
from ml.lmi.temporal import rolling_z

__all__ = ["DetectionRun", "detect_for", "run_detection", "signal_series"]


@dataclass
class DetectionRun:
    as_of: date
    members: int = 0
    with_history: int = 0
    alarms: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "members": self.members,
            "with_history": self.with_history,
            "alarms": len(self.alarms),
            "confirmed": sum(1 for a in self.alarms if a["stats"]["confirmed"]),
            "detections": self.alarms,
        }


def signal_series(history: MemberHistory, signal: str) -> list[tuple[date, float]]:
    """The raw series for one signal, oldest first.

    Returns nothing for a signal this member has no data for, which is
    different from a series of zeros: a member with no salary deduction has not
    had one stop.
    """
    if signal == "days_to_pay":
        return [
            (event.due_date, float(event.days_to_pay))
            for event in history.due_events
            if event.days_to_pay is not None
        ]
    if signal == "deduction_received_ratio":
        return [
            (deduction.cycle, deduction.actual / deduction.expected)
            for deduction in history.deductions
            if deduction.expected > 0
        ]
    if signal == "savings_balance":
        return [(point.at, point.balance) for point in history.savings]
    return []


def detect_for(history: MemberHistory, config: LmiConfig | None = None) -> list[tuple[str, Alarm]]:
    """Every change this member's history shows, across every signal."""
    settings = config or load_config()
    found: list[tuple[str, Alarm]] = []

    for signal in settings.signals:
        series = signal_series(history, signal)
        if len(series) < 8:
            continue
        # Causal: each observation is judged against the ones before it. A
        # baseline over the whole series is lookahead, and on a drifting member
        # it is lookahead in the worst direction, because their median is
        # pulled up by the drift being looked for.
        points = rolling_z(series, signal)
        if not points:
            continue
        raw = [value for _, value in series[-len(points) :]]
        for alarm in run_detect(points, settings.signal(signal), raw=raw):
            found.append((signal, alarm))

    return found


async def run_detection(
    db: AsyncSession,
    *,
    as_of: date,
    member_ids: list[str] | None = None,
    since: date | None = None,
) -> DetectionRun:
    """Detect over every member in scope and emit what changed.

    `since` limits the emitted events to changes that began recently, so a
    nightly run does not re-announce a drift that started last year and was
    dealt with. The detection itself always reads the whole history, because a
    change-point cannot be found from a fragment of the series it sits in.
    """
    settings = load_config()
    run = DetectionRun(as_of=as_of)
    histories = await load_histories(db, as_of=as_of, member_ids=member_ids)
    run.members = len(histories)

    for member_id, history in histories.items():
        alarms = detect_for(history, settings)
        if not alarms:
            continue
        run.with_history += 1
        for _signal, alarm in alarms:
            if since is not None and alarm.at < since:
                continue
            body = {"member_id": member_id, **alarm.as_dict()}
            run.alarms.append(body)
            await emit(
                db,
                "behaviour.change_point_detected",
                body,
                key=member_id,
                producer="lmi",
            )

    return run


async def latest_features(db: AsyncSession, *, as_of: date, limit: int = 5000) -> list[dict[str, Any]]:
    """The materialised feature vectors for a day, for the anomaly detector."""
    rows = (
        (
            await db.execute(
                text("""
        SELECT member_id, features FROM app_lmi.temporal_features
         WHERE as_of = :as_of ORDER BY member_id LIMIT :limit
    """),
                {"as_of": as_of, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    import json

    return [
        {
            "member_id": row["member_id"],
            "features": json.loads(row["features"]) if isinstance(row["features"], str) else row["features"],
        }
        for row in rows
    ]
