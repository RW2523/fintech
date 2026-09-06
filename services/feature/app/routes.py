"""feature-service endpoints (docs/08 §5)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app import repository
from app.compute import compute_features
from app.db import session
from app.registry import FEATURES, REGISTRY_VERSION, PermittedUse, families
from cio_common.errors import NotFound, ValidationFailed

router = APIRouter(prefix="/features", tags=["feature"])


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_id: str = Field(pattern="^M-[0-9]{6}$")
    account_id: str | None = None
    as_of: datetime | None = None
    proposed_instalment: float | None = Field(default=None, ge=0)
    purpose: str = Field(default="UNDERWRITING")
    #: Return an equivalent existing snapshot instead of writing a new one.
    reuse_equivalent: bool = True


@router.get("/registry", summary="The declared features")
async def get_registry(purpose: str | None = None) -> dict[str, Any]:
    """docs/07 §2.1 — a feature not declared here cannot enter a snapshot."""
    definitions = FEATURES
    if purpose:
        try:
            use = PermittedUse(purpose.upper())
        except ValueError as exc:
            raise ValidationFailed(
                f"unknown purpose {purpose!r}", purposes=[str(u) for u in PermittedUse]
            ) from exc
        definitions = tuple(d for d in FEATURES if d.permits(use))

    return {
        "registry_version": REGISTRY_VERSION,
        "count": len(definitions),
        "families": families(),
        "features": [d.as_row() for d in definitions],
    }


@router.post("/snapshot", summary="Compute and store a feature snapshot")
async def create_snapshot(body: SnapshotRequest) -> dict[str, Any]:
    try:
        purpose = PermittedUse(body.purpose.upper())
    except ValueError as exc:
        raise ValidationFailed(f"unknown purpose {body.purpose!r}") from exc

    as_of = body.as_of or datetime.now(UTC)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)

    async with session() as db:
        try:
            snapshot = await compute_features(
                db,
                body.member_id,
                as_of=as_of,
                account_id=body.account_id,
                proposed_instalment=body.proposed_instalment,
                purpose=purpose,
            )
        except KeyError as exc:
            raise NotFound(str(exc)) from exc

        if body.reuse_equivalent:
            existing = await repository.find_equivalent(db, body.member_id, snapshot.inputs_digest)
            if existing:
                stored = await repository.load_snapshot(db, existing)
                return {**stored, "reused": True}  # type: ignore[dict-item]

        snapshot_id = await repository.save_snapshot(db, snapshot)
        stored = await repository.load_snapshot(db, snapshot_id)

    return {**stored, "reused": False}  # type: ignore[dict-item]


@router.get("/{snapshot_id}", summary="A stored feature snapshot")
async def get_snapshot(snapshot_id: str) -> dict[str, Any]:
    async with session() as db:
        stored = await repository.load_snapshot(db, snapshot_id)
    if stored is None:
        raise NotFound(f"no feature snapshot {snapshot_id!r}")
    return stored


@router.post("/registry/publish", summary="Publish the registry to the database")
async def publish_registry() -> dict[str, Any]:
    async with session() as db:
        count = await repository.save_registry(db)
    return {"published": count, "registry_version": REGISTRY_VERSION}
