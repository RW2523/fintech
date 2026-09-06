"""risk-service endpoints (docs/07 §2.3, docs/08 §5)."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app import repository
from app.db import session
from app.scoring import Scorer
from app.snapshots import HttpSnapshotSource, SnapshotSource
from cio_common.errors import NotFound

router = APIRouter(tags=["risk"])

#: Swapped for a static source in tests.
_source: SnapshotSource | None = None


def snapshot_source() -> SnapshotSource:
    global _source
    if _source is None:
        _source = HttpSnapshotSource(os.environ.get("FEATURE_URL"))
    return _source


def set_snapshot_source(source: SnapshotSource | None) -> None:
    global _source
    _source = source


class ScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(min_length=3, max_length=64)
    #: Pin an older model version. Omitted, the version `latest.txt` names is used.
    model_version: str | None = None


@router.post("/risk/score", summary="Score a feature snapshot")
async def score(body: ScoreRequest) -> dict[str, Any]:
    result = await Scorer(snapshot_source(), version=body.model_version).score(body.snapshot_id)
    async with session() as db:
        await repository.save(db, result)
    return result.as_contract()


@router.get("/risk/runs/{model_run_id}", summary="A recorded model run")
async def get_run(model_run_id: str) -> dict[str, Any]:
    async with session() as db:
        found = await repository.find(db, model_run_id)
    if found is None:
        raise NotFound(f"no model run {model_run_id}")
    return found


@router.get("/risk/members/{member_id}/runs", summary="Recent runs for a member")
async def member_runs(member_id: str, limit: int = 20) -> dict[str, Any]:
    async with session() as db:
        rows = await repository.recent_for_member(db, member_id, limit=min(limit, 100))
    return {"member_id": member_id, "count": len(rows), "runs": rows}
