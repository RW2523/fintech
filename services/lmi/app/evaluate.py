"""The nightly pass: detect, decide, alert (docs/07 §4.5-4.7).

One run over the book that reads what each member did, asks the detector
whether anything changed, asks the state machine what that means, and raises an
alert when a person should look.

Deliberately one place rather than three services calling each other. The three
steps have to see the same evidence: a state change justified by a change-point
the alert does not mention is a case an officer cannot follow.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.alerts import Alert, close_on_recovery, raise_alert, shortlist
from app.changepoint import Alarm, detect
from app.config import load_config
from app.detect import signal_series
from app.materialise import MemberHistory, load_histories
from app.state import Evidence, next_state
from cio_common.outbox import emit
from ml.lmi.dataset import LATE_DAYS, features_at
from ml.lmi.temporal import rolling_z

__all__ = ["EvaluationRun", "evaluate_book", "evidence_for"]


@dataclass
class EvaluationRun:
    as_of: date
    members: int = 0
    evaluated: int = 0
    transitions: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        by_state: dict[str, int] = {}
        for move in self.transitions:
            by_state[move["to_state"]] = by_state.get(move["to_state"], 0) + 1
        return {
            "as_of": self.as_of.isoformat(),
            "members": self.members,
            "evaluated": self.evaluated,
            "transitions": len(self.transitions),
            "by_state": by_state,
            "alerts_raised": len(self.alerts),
            "alerts_closed": len(self.closed),
            "seconds": round(self.seconds, 2),
        }


def evidence_for(
    member_id: str,
    history: MemberHistory,
    *,
    as_of: date,
    alarms: list[Alarm],
    features: dict[str, float],
    p30: float | None = None,
    p90: float | None = None,
    outage_windows: list[tuple[date, date]] | None = None,
    arrangement_active: bool = False,
    fraud_level: str = "NONE",
) -> Evidence:
    """Everything the state machine is allowed to look at, assembled once."""
    past = [event for event in history.due_events if event.due_date < as_of]

    on_time = 0
    for event in reversed(past[-4:]):
        if event.days_to_pay is not None and event.days_to_pay <= LATE_DAYS:
            on_time += 1
        else:
            break

    timings = [
        (event.due_date, float(event.days_to_pay))
        for event in history.due_events
        if event.days_to_pay is not None
    ]
    points = rolling_z(timings, "days_to_pay") if len(timings) >= 8 else []

    departures = 0
    for at, z in reversed(points):
        if at >= as_of:
            continue
        if z > 2.0:
            departures += 1
        else:
            break

    return Evidence(
        member_id=member_id,
        as_of=as_of,
        features=features,
        change_points=[alarm.as_dict() for alarm in alarms if alarm.at < as_of],
        p30=p30,
        p90=p90,
        max_days_late=max((event.days_to_pay or 0) for event in past[-3:]) if past[-3:] else 0,
        consecutive_on_time=on_time,
        departure_events=departures,
        fraud_level=fraud_level,
        outage_covers_deviations=_explained_by_outage(past, outage_windows or [], as_of),
        arrangement_active=arrangement_active,
        employer_gap=bool(features.get("employer_gap_flag", 0)),
        distance_to_baseline=[abs(z) for at, z in points if at < as_of][-2:],
    )


def _explained_by_outage(events: list[Any], windows: list[tuple[date, date]], as_of: date) -> bool:
    """Whether every recent late settlement falls inside an outage.

    Every, not any. A member with one late payment during an outage and three
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


async def _outage_windows(db: AsyncSession) -> list[tuple[date, date]]:
    rows = (await db.execute(text("SELECT from_ts, to_ts FROM core.outage_window"))).mappings().all()
    return [(row["from_ts"].date(), row["to_ts"].date()) for row in rows]


async def _exposures(db: AsyncSession) -> dict[str, tuple[str | None, float]]:
    """Each member's largest live account and what is outstanding on it.

    The largest rather than the sum: an alert is about an account, and ranking
    by a member's total would put somebody with four small facilities above
    somebody with one large one.
    """
    rows = (
        (
            await db.execute(
                text("""
        SELECT DISTINCT ON (member_id) member_id, account_id, principal
          FROM core.account WHERE status = 'ACTIVE'
         ORDER BY member_id, principal DESC
    """)
            )
        )
        .mappings()
        .all()
    )
    return {row["member_id"]: (row["account_id"], float(row["principal"])) for row in rows}


async def evaluate_book(
    db: AsyncSession,
    *,
    as_of: date,
    member_ids: list[str] | None = None,
    model: Any = None,
    cap: int = 25,
    officers: int = 1,
) -> EvaluationRun:
    """Walk every member in scope through detection, state and alerting."""
    started = time.perf_counter()
    settings = load_config()
    run = EvaluationRun(as_of=as_of)

    histories = await load_histories(db, as_of=as_of, member_ids=member_ids)
    windows = await _outage_windows(db)
    exposures = await _exposures(db)
    run.members = len(histories)

    raised: list[Alert] = []
    for member_id, history in histories.items():
        features = features_at(history.due_events, history.deductions, history.savings, as_of=as_of)
        if features is None:
            continue
        run.evaluated += 1

        alarms: list[Alarm] = []
        for signal in settings.signals:
            series = signal_series(history, signal)
            if len(series) < 8:
                continue
            points = rolling_z(series, signal)
            if not points:
                continue
            alarms.extend(
                detect(
                    points,
                    settings.signal(signal),
                    raw=[value for _, value in series[-len(points) :]],
                )
            )

        p30 = p90 = None
        if model is not None:
            scored = {score.horizon: score.probability for score in model.score(features)}
            p30, p90 = scored.get(30), scored.get(90)

        stored = await repository.current_state(db, member_id)
        evidence = evidence_for(
            member_id,
            history,
            as_of=as_of,
            alarms=alarms,
            features=features,
            p30=p30,
            p90=p90,
            outage_windows=windows,
        )
        move = next_state(
            stored["state"] if stored else "STABLE",
            evidence,
            since=stored["since"] if stored else None,
        )
        if not move.changed:
            continue

        await repository.save_transition(db, move)
        run.transitions.append(move.as_dict())
        await emit(
            db,
            "member.state_changed",
            move.as_dict(),
            key=member_id,
            producer="lmi",
        )

        # Coming back closes what going out opened.
        if move.to_state == "STABLE" and move.from_state == "RECOVERY":
            for stored_alert in await repository.open_alerts(db, member_id=member_id):
                await repository.close_alert(
                    db,
                    stored_alert["alert_id"],
                    at=as_of,
                    reason="the member returned to STABLE",
                )
                run.closed.append(stored_alert["alert_id"])

        account_id, exposure = exposures.get(member_id, (None, 0.0))
        alert = raise_alert(
            move.as_dict(),
            features=features,
            exposure=exposure,
            p90=p90,
            account_id=account_id,
            change_point=evidence.latest_change_point,
            opened_at=as_of,
        )
        if alert is not None:
            raised.append(alert)

    # The cap decides what reaches a queue today; everything raised is stored,
    # because a capped alert is deferred rather than dropped.
    for alert in raised:
        await repository.save_alert(db, alert)
    for alert in shortlist(raised, cap=cap, officers=officers):
        run.alerts.append(alert.as_dict())
        await emit(
            db,
            "early_warning.case_created",
            alert.as_dict(),
            key=alert.member_id,
            producer="lmi",
        )

    run.seconds = time.perf_counter() - started
    return run


__all__ += ["close_on_recovery"]
