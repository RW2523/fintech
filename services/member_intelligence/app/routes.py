"""member-intelligence-service endpoints (docs/08 §3)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.db import session
from app.events import EVENT_TYPES
from app.importer import import_members
from app.projection import build_profile, load_profile, save_profile
from cio_common.errors import NotFound, ValidationFailed

router = APIRouter(tags=["member"])

MAX_PAGE = 500


class ImportRequest(BaseModel):
    """Seed or refresh timelines (docs/08 §3)."""

    model_config = ConfigDict(extra="forbid")

    member_ids: list[str] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1)
    rebuild_profiles: bool = True


def _cursor(occurred_at: datetime, event_id: str) -> str:
    return base64.urlsafe_b64encode(f"{occurred_at.isoformat()}|{event_id}".encode()).decode()


def _decode(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        when, _, event_id = raw.partition("|")
        return datetime.fromisoformat(when), event_id
    except Exception as exc:
        raise ValidationFailed("cursor is not readable") from exc


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _timestamp(value: str | None, field: str) -> datetime | None:
    """Parse in Python: asyncpg wants a datetime once the cast names the type."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationFailed(f"{field} is not an ISO timestamp: {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@router.post("/members/import", summary="Build timelines from the core records")
async def run_import(body: ImportRequest) -> dict[str, Any]:
    async with session() as db:
        members = body.member_ids or None
        if members is None and body.limit:
            from app.importer import member_ids

            members = await member_ids(db, limit=body.limit)

        summary = await import_members(db, members)

        if body.rebuild_profiles:
            targets = members
            if targets is None:
                from app.importer import member_ids

                targets = await member_ids(db)
            for member_id in targets:
                profile = await build_profile(db, member_id)
                if profile:
                    await save_profile(db, profile)

    return summary.as_dict()


@router.get("/members/{member_id}/profile", summary="The member projection")
async def get_profile(member_id: str, refresh: bool = False) -> dict[str, Any]:
    async with session() as db:
        if not refresh:
            stored = await load_profile(db, member_id)
            if stored:
                return stored
        profile = await build_profile(db, member_id)
        if not profile:
            raise NotFound(f"no member {member_id!r}")
        await save_profile(db, profile)
    return profile


@router.get("/members/{member_id}/timeline", summary="A page of the member's events")
async def get_timeline(
    member_id: str,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    types: str | None = Query(default=None, description="comma-separated event types"),
    cursor: str | None = None,
    limit: int = Query(default=100, ge=1, le=MAX_PAGE),
) -> dict[str, Any]:
    wanted: list[str] = []
    if types:
        wanted = [t.strip().upper() for t in types.split(",") if t.strip()]
        unknown = [t for t in wanted if t not in EVENT_TYPES]
        if unknown:
            raise ValidationFailed(f"unknown event types {unknown}", known=sorted(EVENT_TYPES))

    after_at: datetime | None = None
    after_id = ""
    if cursor:
        after_at, after_id = _decode(cursor)

    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT event_id, account_id, occurred_at, event_type, source_system,
                   source_record_id, payload, data_quality, permitted_uses
            FROM app_member.member_event
            WHERE member_id = :member_id
              AND (CAST(:from_ts AS timestamptz) IS NULL
                   OR occurred_at >= CAST(:from_ts AS timestamptz))
              AND (CAST(:to_ts AS timestamptz) IS NULL
                   OR occurred_at <= CAST(:to_ts AS timestamptz))
              AND (CAST(:types AS text[]) IS NULL
                   OR event_type = ANY(CAST(:types AS text[])))
              AND (CAST(:after_at AS timestamptz) IS NULL
                   OR (occurred_at, event_id) > (CAST(:after_at AS timestamptz),
                                                 CAST(:after_id AS text)))
            ORDER BY occurred_at, event_id
            LIMIT :limit
        """),
                    {
                        "member_id": member_id,
                        "from_ts": _timestamp(from_, "from"),
                        "to_ts": _timestamp(to, "to"),
                        "types": wanted or None,
                        "after_at": after_at,
                        "after_id": after_id or None,
                        "limit": limit,
                    },
                )
            )
            .mappings()
            .all()
        )

    events = [
        {
            "event_id": r["event_id"],
            "member_id": member_id,
            "account_id": r["account_id"],
            "occurred_at": r["occurred_at"].isoformat().replace("+00:00", "Z"),
            "event_type": r["event_type"],
            "source_system": r["source_system"],
            "source_record_id": r["source_record_id"],
            "payload": _json(r["payload"]),
            "data_quality": _json(r["data_quality"]),
            "permitted_uses": list(r["permitted_uses"]),
        }
        for r in rows
    ]
    next_cursor = _cursor(rows[-1]["occurred_at"], rows[-1]["event_id"]) if len(rows) == limit else None

    return {"member_id": member_id, "events": events, "count": len(events), "next_cursor": next_cursor}


@router.get("/members/{member_id}/history", summary="The history.get tool payload")
async def get_history(member_id: str) -> dict[str, Any]:
    """docs/06 §4 — what the credit_risk agent reads."""
    async with session() as db:
        profile = await load_profile(db, member_id) or await build_profile(db, member_id)
    if not profile:
        raise NotFound(f"no member {member_id!r}")

    history = profile["history"]
    return {
        "member_id": member_id,
        "ontime_rate_24m": history["ontime_rate_24m"],
        "arrears_12m": history["arrears_12m"],
        "months_since_last_arrears": history["months_since_last_arrears"],
        "restructures_36m": 0,
        "facilities": profile["accounts"],
        "history_months": profile["tenure_months"],
    }


@router.get("/members/{member_id}/savings", summary="Savings series")
async def get_savings(member_id: str, months: int = Query(default=24, ge=1, le=120)) -> dict[str, Any]:
    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT occurred_at, (payload->>'balance')::numeric AS balance
            FROM app_member.member_event
            WHERE member_id = :member_id AND event_type = 'SAVINGS_BALANCE'
            ORDER BY occurred_at DESC LIMIT :months
        """),
                    {"member_id": member_id, "months": months},
                )
            )
            .mappings()
            .all()
        )
    series = [
        {"as_of": r["occurred_at"].date().isoformat(), "balance": f"{r['balance']:.2f}"}
        for r in reversed(rows)
    ]
    return {"member_id": member_id, "series": series}


@router.get("/members/{member_id}/shares", summary="Share capital series")
async def get_shares(member_id: str) -> dict[str, Any]:
    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT occurred_at, (payload->>'units')::int AS units,
                   (payload->>'value')::numeric AS value
            FROM app_member.member_event
            WHERE member_id = :member_id AND event_type = 'SHARE_CAPITAL'
            ORDER BY occurred_at
        """),
                    {"member_id": member_id},
                )
            )
            .mappings()
            .all()
        )
    return {
        "member_id": member_id,
        "series": [
            {"as_of": r["occurred_at"].date().isoformat(), "units": r["units"], "value": f"{r['value']:.2f}"}
            for r in rows
        ],
    }


@router.get("/members/{member_id}/state", summary="The member's LMI state")
async def get_state(member_id: str) -> dict[str, Any]:
    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT account_id, state, since, prev_state, reason
            FROM app_member.member_state WHERE member_id = :member_id
        """),
                    {"member_id": member_id},
                )
            )
            .mappings()
            .all()
        )
    return {
        "member_id": member_id,
        "states": [
            {
                "account_id": r["account_id"],
                "state": r["state"],
                "since": r["since"].isoformat(),
                "prev_state": r["prev_state"],
                "reason": _json(r["reason"]),
            }
            for r in rows
        ],
    }


@router.get("/members/{member_id}/stats", summary="Event counts by type")
async def get_stats(member_id: str) -> dict[str, Any]:
    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT event_type, count(*) AS n FROM app_member.member_event
            WHERE member_id = :member_id GROUP BY event_type ORDER BY event_type
        """),
                    {"member_id": member_id},
                )
            )
            .mappings()
            .all()
        )
    return {
        "member_id": member_id,
        "by_type": {r["event_type"]: r["n"] for r in rows},
        "total": sum(r["n"] for r in rows),
        "as_of": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
