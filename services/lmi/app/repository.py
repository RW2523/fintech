"""Persistence for member state and alerts (docs/07 §4.5, §4.7)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import Alert
from app.state import Transition
from cio_common.ids import derived_id

__all__ = [
    "close_alert",
    "current_state",
    "load_alerts",
    "member_states",
    "open_alerts",
    "save_alert",
    "save_transition",
    "transitions_for",
]


async def current_state(db: AsyncSession, member_id: str) -> dict[str, Any] | None:
    row = (
        (
            await db.execute(
                text("SELECT * FROM app_lmi.member_state WHERE member_id = :m"),
                {"m": member_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def state_counts(db: AsyncSession) -> dict[str, int]:
    """How many members stand in each state right now.

    A count rather than a list, because this backs a dashboard panel and a
    gauge with one series per member is a gauge that takes Prometheus down.
    """
    rows = (
        await db.execute(text("SELECT state, count(*) AS members FROM app_lmi.member_state GROUP BY state"))
    ).mappings()
    return {str(row["state"]): int(row["members"]) for row in rows}


async def member_states(
    db: AsyncSession, *, state: str | None = None, limit: int = 500
) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                text("""
        SELECT * FROM app_lmi.member_state
         WHERE (CAST(:state AS text) IS NULL OR state = CAST(:state AS text))
         ORDER BY since DESC LIMIT :limit
    """),
                {"state": state, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def save_transition(db: AsyncSession, transition: Transition) -> str | None:
    """Record a move and update where the member stands.

    A transition that did not change anything is not written: the table is the
    history of what happened, and a row per nightly evaluation would bury the
    handful of rows that matter.
    """
    if not transition.changed:
        return None

    transition_id = derived_id("evt", transition.member_id, transition.at.isoformat(), transition.to_state)
    await db.execute(
        text("""
        INSERT INTO app_lmi.state_transition
          (transition_id, member_id, at, from_state, to_state, rule, reason,
           corroboration, evidence_ids)
        VALUES (:id, :member_id, :at, :from_state, :to_state, :rule, :reason,
                CAST(:corroboration AS jsonb), CAST(:evidence_ids AS jsonb))
        ON CONFLICT (transition_id) DO NOTHING
    """),
        {
            "id": transition_id,
            "member_id": transition.member_id,
            "at": transition.at,
            "from_state": transition.from_state,
            "to_state": transition.to_state,
            "rule": transition.rule,
            "reason": transition.reason,
            "corroboration": json.dumps(transition.corroboration.as_dict()),
            "evidence_ids": json.dumps(transition.evidence_ids),
        },
    )
    await db.execute(
        text("""
        INSERT INTO app_lmi.member_state (member_id, state, since, rule, reason)
        VALUES (:member_id, :state, :since, :rule, :reason)
        ON CONFLICT (member_id) DO UPDATE SET
          state = EXCLUDED.state, since = EXCLUDED.since, rule = EXCLUDED.rule,
          reason = EXCLUDED.reason, updated_at = now()
    """),
        {
            "member_id": transition.member_id,
            "state": transition.to_state,
            "since": transition.at,
            "rule": transition.rule,
            "reason": transition.reason,
        },
    )
    return transition_id


async def transitions_for(db: AsyncSession, member_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """How a member reached where they are, newest first."""
    rows = (
        (
            await db.execute(
                text("""
        SELECT * FROM app_lmi.state_transition
         WHERE member_id = :m ORDER BY at DESC LIMIT :limit
    """),
                {"m": member_id, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    out = []
    for row in rows:
        body = dict(row)
        for field in ("corroboration", "evidence_ids"):
            if isinstance(body.get(field), str):
                body[field] = json.loads(body[field])
        out.append(body)
    return out


async def save_alert(db: AsyncSession, alert: Alert) -> None:
    """Store an alert. Raising the same concern twice updates the first."""
    await db.execute(
        text("""
        INSERT INTO app_lmi.alert
          (alert_id, member_id, account_id, state, signals, rank_value, why_now,
           p90, exposure, change_point, corroboration, opened_at)
        VALUES (:alert_id, :member_id, :account_id, :state, CAST(:signals AS jsonb),
                :rank_value, :why_now, :p90, :exposure, :change_point,
                CAST(:corroboration AS jsonb), :opened_at)
        ON CONFLICT (alert_id) DO UPDATE SET
          rank_value = EXCLUDED.rank_value, why_now = EXCLUDED.why_now,
          p90 = EXCLUDED.p90, exposure = EXCLUDED.exposure,
          corroboration = EXCLUDED.corroboration
    """),
        {
            "alert_id": alert.alert_id,
            "member_id": alert.member_id,
            "account_id": alert.account_id,
            "state": alert.state,
            "signals": json.dumps(list(alert.signals)),
            "rank_value": alert.rank_value,
            "why_now": alert.why_now,
            "p90": alert.p90,
            "exposure": alert.exposure,
            "change_point": date.fromisoformat(alert.change_point) if alert.change_point else None,
            "corroboration": json.dumps(alert.corroboration),
            "opened_at": alert.opened_at or date.today(),
        },
    )


async def open_alerts(
    db: AsyncSession, *, member_id: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                text("""
        SELECT * FROM app_lmi.alert
         WHERE closed_at IS NULL
           AND (CAST(:member_id AS text) IS NULL OR member_id = CAST(:member_id AS text))
         ORDER BY rank_value DESC LIMIT :limit
    """),
                {"member_id": member_id, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    return [_alert_row(row) for row in rows]


async def load_alerts(db: AsyncSession, member_id: str) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                text("SELECT * FROM app_lmi.alert WHERE member_id = :m ORDER BY opened_at DESC"),
                {"m": member_id},
            )
        )
        .mappings()
        .all()
    )
    return [_alert_row(row) for row in rows]


async def close_alert(db: AsyncSession, alert_id: str, *, at: date, reason: str) -> bool:
    """Close one alert. Returns whether it was open."""
    result = await db.execute(
        text("""
        UPDATE app_lmi.alert SET closed_at = :at, close_reason = :reason
         WHERE alert_id = :id AND closed_at IS NULL
        RETURNING alert_id
    """),
        {"id": alert_id, "at": at, "reason": reason},
    )
    return result.first() is not None


def _alert_row(row: Any) -> dict[str, Any]:
    body = dict(row)
    for field in ("signals", "corroboration"):
        if isinstance(body.get(field), str):
            body[field] = json.loads(body[field])
    for field in ("opened_at", "closed_at", "change_point"):
        if body.get(field):
            body[field] = body[field].isoformat()
    body["exposure"] = float(body.get("exposure") or 0)
    return body
