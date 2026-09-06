"""audit-service endpoints (docs/04 §6, docs/09 §6).

Two jobs. It holds the audit trail: what happened, who asked, and the state
either side. And it reconstructs a case for a person who has to answer for a
decision, which means gathering the ledger, the opinions, the documents and the
audit trail into one timeline.

The reconstruction is a read across services rather than a store of its own. A
second copy of the history is a second thing that can disagree with the first,
and the whole value of this screen is that it does not.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Query, Request

from app import trail
from app.db import session
from app.models import AuditWrite, ExportRequest
from app.worm import export_for, worm
from cio_common.errors import CioError, NotFound

router = APIRouter(tags=["audit"])

#: Where the pieces of a case live. Read, never cached: a reconstruction that
#: served a stale copy would be answering about a case that no longer exists.
UPSTREAMS = {
    "decision": os.environ.get("DECISION_URL", "http://decision:8012"),
    "committee": os.environ.get("COMMITTEE_URL", "http://committee:8009"),
    "document": os.environ.get("DOCUMENT_URL", "http://document:8002"),
    "execution": os.environ.get("EXECUTION_URL", "http://execution:8013"),
    "application": os.environ.get("APPLICATION_URL", "http://application:8001"),
}


# ---------------------------------------------------------------------------
# the trail
# ---------------------------------------------------------------------------
@router.post("/audit", summary="Append one audit entry")
async def write(body: AuditWrite, request: Request) -> dict[str, Any]:
    payload = body.model_dump()
    # The trace id is taken from the request when the caller did not set one,
    # so an entry can always be tied back to the call that produced it.
    payload["trace_id"] = payload.get("trace_id") or request.headers.get("x-trace-id")
    async with session() as db:
        entry = await trail.append(db, payload)
        await db.commit()
    return {"entry_id": entry.entry_id, "seq": entry.seq, "hash": entry.hash}


@router.get("/audit", summary="The trail, filtered")
async def read(
    case_id: str | None = None,
    actor_id: str | None = None,
    action: str | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, Any]:
    async with session() as db:
        entries = await trail.entries_for(db, case_id=case_id, actor_id=actor_id, action=action, limit=limit)
    return {"count": len(entries), "entries": [e.as_dict() for e in entries]}


@router.get("/audit/verify", summary="Recompute the audit chain")
async def verify(start: int | None = None, end: int | None = None) -> dict[str, Any]:
    async with session() as db:
        checked, breaks = await trail.verify(db, start=start, end=end)
    return {
        "verified": not breaks,
        "entries_checked": checked,
        "breaks": [b.as_dict() for b in breaks],
    }


# ---------------------------------------------------------------------------
# write-once export
# ---------------------------------------------------------------------------
@router.post("/audit/export", summary="Export a day to the write-once bucket")
async def export(body: ExportRequest) -> dict[str, Any]:
    """docs/04 §6 — daily export with object lock.

    Yesterday by default: today is still being written to, and exporting a
    partial day would leave the rest of it with nowhere to go.
    """
    day = date.fromisoformat(body.day) if body.day else (datetime.now(UTC).date() - timedelta(days=1))

    async with session() as db:
        entries = [
            e.as_dict()
            for e in await trail.entries_for(db, limit=100_000)
            if e.created_at and e.created_at.date() == day
        ]
        head = await trail.head(db)

    store = worm()
    try:
        locked = store.ensure_bucket()
        result = export_for(day, entries, head, locked)
        store.put(result.object_key, _serialise(entries), locked=locked)
    except Exception as exc:
        raise CioError(f"the write-once bucket refused the export: {exc}", day=day.isoformat()) from exc

    async with session() as db:
        await db.execute(
            _upsert_export(),
            {
                "export_id": result.export_id,
                "day": day,
                "first_seq": result.first_seq,
                "last_seq": result.last_seq,
                "entries": result.entries,
                "object_key": result.object_key,
                "sha256": result.sha256,
                "head_hash": result.head_hash,
            },
        )
        await db.commit()

    return {
        **result.as_dict(),
        # Said plainly rather than left in a log: an export the storage did not
        # lock is a backup, not a WORM archive, and the difference matters to
        # whoever has to rely on it.
        "warning": None if result.locked else "the bucket has no object lock; this is a copy, not WORM",
    }


@router.get("/audit/exports", summary="What has been exported")
async def exports(limit: int = Query(default=90, ge=1, le=3650)) -> dict[str, Any]:
    from sqlalchemy import text

    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("SELECT * FROM audit.export ORDER BY day DESC LIMIT :limit"),
                    {"limit": limit},
                )
            )
            .mappings()
            .all()
        )
    return {"count": len(rows), "exports": [dict(row) for row in rows]}


def _upsert_export() -> Any:
    from sqlalchemy import text

    return text("""
        INSERT INTO audit.export
          (export_id, day, first_seq, last_seq, entries, object_key, sha256, head_hash)
        VALUES (:export_id, :day, :first_seq, :last_seq, :entries, :object_key, :sha256, :head_hash)
        ON CONFLICT (day) DO UPDATE SET
          export_id = EXCLUDED.export_id, first_seq = EXCLUDED.first_seq,
          last_seq = EXCLUDED.last_seq, entries = EXCLUDED.entries,
          object_key = EXCLUDED.object_key, sha256 = EXCLUDED.sha256,
          head_hash = EXCLUDED.head_hash, created_at = now()
    """)


def _serialise(entries: list[dict[str, Any]]) -> bytes:
    from app.worm import serialise

    return serialise(entries)


# ---------------------------------------------------------------------------
# reconstruction (docs/09 §6)
# ---------------------------------------------------------------------------
async def _get(client: httpx.AsyncClient, url: str, **params: Any) -> Any:
    """A read that reports absence rather than inventing emptiness."""
    try:
        response = await client.get(url, params={k: v for k, v in params.items() if v is not None})
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    return response.json()


@router.get("/reconstruct/{case_id}", summary="One case, end to end")
async def reconstruct(case_id: str) -> dict[str, Any]:
    """docs/09 §6 — the timeline a person answers questions from.

    Everything is fetched concurrently and everything that could not be read is
    named. A timeline with a silent gap in it is worse than no timeline: the
    reader would take the gap for an absence of events.
    """
    async with httpx.AsyncClient(timeout=20.0) as client:
        ledger, audit_entries, documents, findings, actions = await asyncio.gather(
            _get(client, f"{UPSTREAMS['decision']}/ledger", case_id=case_id),
            _read_trail(case_id),
            _get(client, f"{UPSTREAMS['document']}/cases/{case_id}/documents"),
            _get(client, f"{UPSTREAMS['document']}/cases/{case_id}/findings"),
            _get(client, f"{UPSTREAMS['execution']}/actions", case_id=case_id),
        )

        entries = list((ledger or {}).get("entries") or [])
        run_ids = sorted(
            {
                str(entry["payload"].get("committee_run_id"))
                for entry in entries
                if isinstance(entry.get("payload"), dict) and entry["payload"].get("committee_run_id")
            }
        )
        opinions: list[dict[str, Any]] = []
        for run_id in run_ids:
            answered = await _get(client, f"{UPSTREAMS['committee']}/committee/runs/{run_id}/opinions")
            opinions.extend((answered or {}).get("opinions") or [])

        verified = await _get(client, f"{UPSTREAMS['decision']}/ledger/verify")

    if not entries and not audit_entries:
        raise NotFound(f"nothing is recorded for {case_id!r}")

    unavailable = [
        name
        for name, value in (
            ("ledger", ledger),
            ("documents", documents),
            ("findings", findings),
            ("actions", actions),
            ("chain_verification", verified),
        )
        if value is None
    ]

    return {
        "case_id": case_id,
        "timeline": _timeline(entries, opinions, audit_entries),
        "documents": (documents or {}).get("documents") or [],
        "findings": (findings or {}).get("findings") or [],
        "actions": (actions or {}).get("actions") or [],
        "chain": _chain(verified),
        "unavailable": unavailable,
    }


def _chain(verified: dict[str, Any] | None) -> dict[str, Any]:
    """One shape for the verdict, whatever the ledger called it.

    The decision service reports `intact`; a reader of this timeline should not
    have to know that, and a UI that looked for the wrong key would silently
    show "not verified" on a chain that verifies.
    """
    if verified is None:
        return {"verified": None, "entries_checked": 0, "breaks": []}
    verdict = verified.get("verified")
    if verdict is None:
        verdict = verified.get("intact")
    return {
        "verified": verdict,
        "entries_checked": verified.get("entries_checked", 0),
        "breaks": verified.get("breaks") or [],
        "verified_at": verified.get("verified_at"),
    }


async def _read_trail(case_id: str) -> list[dict[str, Any]]:
    async with session() as db:
        return [e.as_dict() for e in await trail.entries_for(db, case_id=case_id)]


#: docs/09 §6 — the order a case actually happens in. Sorting by timestamp
#: alone interleaves an audit entry written milliseconds later with the ledger
#: entry it describes, which reads as two events rather than one.
KIND_ORDER = {
    "SNAPSHOT": 0,
    "COMMITTEE_RUN": 1,
    "OPINION": 2,
    "DECISION_RECORD": 3,
    "SAMPLE_REVIEW": 4,
    "HUMAN_DECISION": 5,
    "TOKEN": 6,
    "ACTION": 7,
    "OUTCOME": 8,
}


def _timeline(
    entries: list[dict[str, Any]], opinions: list[dict[str, Any]], audit_entries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """One list, in the order the case happened."""
    events: list[dict[str, Any]] = []

    for entry in entries:
        events.append(
            {
                "source": "ledger",
                "kind": entry.get("kind"),
                "at": entry.get("created_at"),
                "seq": entry.get("seq"),
                "entry_id": entry.get("entry_id"),
                "hash": entry.get("hash"),
                "prev_hash": entry.get("prev_hash"),
                "payload": entry.get("payload"),
            }
        )

    for opinion in opinions:
        body = opinion.get("body") if isinstance(opinion, dict) and "body" in opinion else opinion
        events.append(
            {
                "source": "committee",
                "kind": "OPINION",
                "at": (body or {}).get("created_at"),
                "agent_id": (body or {}).get("agent_id"),
                "stance": (body or {}).get("stance"),
                "payload": body,
            }
        )

    for entry in audit_entries:
        events.append(
            {
                "source": "audit",
                "kind": f"AUDIT:{entry.get('action')}",
                "at": entry.get("created_at"),
                "seq": entry.get("seq"),
                "entry_id": entry.get("entry_id"),
                "hash": entry.get("hash"),
                "prev_hash": entry.get("prev_hash"),
                "payload": entry,
            }
        )

    def order(event: dict[str, Any]) -> tuple[str, int]:
        kind = str(event.get("kind") or "")
        return (str(event.get("at") or ""), KIND_ORDER.get(kind, 99))

    return sorted(events, key=order)
