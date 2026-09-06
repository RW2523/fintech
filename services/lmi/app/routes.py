"""lmi-service endpoints (docs/07 §4).

Serves the temporal features and runs the materialisation. Nothing here judges
a member: it reports what their behaviour looked like on a day, and says when
it has too little history to say anything.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.db import session
from app.materialise import materialise
from cio_common.errors import NotFound

router = APIRouter(tags=["lmi"])


class MaterialiseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: str | None = None
    member_ids: list[str] | None = Field(default=None, max_length=10_000)


def _today() -> date:
    return datetime.now(UTC).date()


@router.post("/lmi/materialise", summary="Compute a day's temporal features")
async def run_materialisation(body: MaterialiseRequest) -> dict[str, Any]:
    """docs/07 §4.2 — the nightly job, and the event-triggered one.

    Takes an explicit member list when something happened to those members, and
    the whole book when it is the nightly run. Both write the same rows, so a
    member refreshed at noon is not a different kind of row from one refreshed
    at midnight.
    """
    as_of = date.fromisoformat(body.as_of) if body.as_of else _today()
    async with session() as db:
        run = await materialise(db, as_of=as_of, member_ids=body.member_ids)
        await db.commit()
    return run.as_dict()


@router.get("/lmi/features/{member_id}", summary="One member's temporal features")
async def features(member_id: str, as_of: str | None = None) -> dict[str, Any]:
    """The most recent feature set at or before `as_of`.

    At or before, not exactly on: a member whose features were last computed on
    Tuesday has features on Wednesday, and refusing to answer would make every
    reader implement this fallback themselves.
    """
    cutoff = date.fromisoformat(as_of) if as_of else _today()
    async with session() as db:
        row = (
            (
                await db.execute(
                    text("""
            SELECT * FROM app_lmi.temporal_features
             WHERE member_id = :member_id AND as_of <= :cutoff
             ORDER BY as_of DESC LIMIT 1
        """),
                    {"member_id": member_id, "cutoff": cutoff},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise NotFound(
            f"no temporal features for {member_id!r} at or before {cutoff.isoformat()}",
            member_id=member_id,
        )

    body = dict(row)
    for field in ("features", "baselines", "seasonal"):
        if isinstance(body.get(field), str):
            body[field] = json.loads(body[field])
    body["as_of"] = body["as_of"].isoformat()
    body["computed_at"] = body["computed_at"].isoformat()
    # Said rather than left to be worked out from a date: a reader asking about
    # today and getting Tuesday's numbers should know that is what happened.
    body["stale_days"] = (cutoff - row["as_of"]).days
    return body


@router.get("/lmi/runs", summary="What the materialisation has done")
async def runs(limit: int = Query(default=30, ge=1, le=365)) -> dict[str, Any]:
    """A run that covered half the book is visible as such rather than as a
    quiet night."""
    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("SELECT * FROM app_lmi.materialisation ORDER BY started_at DESC LIMIT :limit"),
                    {"limit": limit},
                )
            )
            .mappings()
            .all()
        )
    return {"count": len(rows), "runs": [dict(row) for row in rows]}
