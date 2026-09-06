"""Recording and reading model runs (docs/04 §4)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.scoring import ScoreResult

__all__ = ["find", "recent_for_member", "save"]

#: Named rather than `SELECT *`, so a column added later cannot leak into a
#: response nobody re-read. Checked at import: the only thing interpolated into
#: a query in this module is this list, and it must be bare identifiers.
_COLUMN_NAMES = (
    "model_run_id",
    "snapshot_id",
    "member_id",
    "model_version",
    "inputs_digest",
    "champion_pd",
    "champion_grade",
    "challenger_pd",
    "challenger_grade",
    "ood_score",
    "conduct_score",
    "conduct_calc_id",
    "reason_codes",
    "drivers",
    "evidence_refs",
    "latency_ms",
    "created_at",
)
assert all(name.isidentifier() for name in _COLUMN_NAMES), _COLUMN_NAMES
_COLUMNS = ", ".join(_COLUMN_NAMES)

#: A run is content-addressed, so re-scoring the same snapshot with the same
#: model produces the same row rather than a second one. `DO NOTHING` keeps the
#: original, which is the record a decision already cited.
_INSERT = text("""
INSERT INTO app_risk.model_run (
  model_run_id, snapshot_id, member_id, model_version, inputs_digest,
  champion_pd, champion_grade, challenger_pd, challenger_grade, ood_score,
  conduct_score, conduct_calc_id, reason_codes, drivers, evidence_refs, latency_ms)
VALUES (
  :model_run_id, :snapshot_id, :member_id, :model_version, :inputs_digest,
  :champion_pd, :champion_grade, :challenger_pd, :challenger_grade, :ood_score,
  :conduct_score, :conduct_calc_id, :reason_codes,
  CAST(:drivers AS jsonb), CAST(:evidence_refs AS jsonb), :latency_ms)
ON CONFLICT (model_run_id) DO NOTHING
RETURNING model_run_id
""")


def _decode(value: Any) -> Any:
    """asyncpg decodes jsonb already; other drivers hand back a string."""
    if isinstance(value, str):
        return json.loads(value)
    return value


async def save(db: AsyncSession, result: ScoreResult) -> bool:
    """Write the run. Returns whether this call was the one that wrote it."""
    prediction = result.prediction
    written = await db.execute(
        _INSERT,
        {
            "model_run_id": prediction.model_run_id,
            "snapshot_id": result.snapshot.snapshot_id,
            "member_id": result.snapshot.member_id,
            "model_version": prediction.version,
            "inputs_digest": prediction.inputs_digest,
            "champion_pd": prediction.champion.pd_12m,
            "champion_grade": prediction.champion.grade,
            "challenger_pd": prediction.challenger.pd_12m,
            "challenger_grade": prediction.challenger.grade,
            "ood_score": prediction.champion.ood_score,
            "conduct_score": prediction.conduct.score,
            "conduct_calc_id": prediction.conduct.calc_id,
            "reason_codes": list(prediction.reason_codes),
            "drivers": json.dumps([d.as_dict() for d in prediction.drivers]),
            "evidence_refs": json.dumps(result.evidence_refs),
            "latency_ms": result.latency_ms,
        },
    )
    await db.commit()
    return written.scalar_one_or_none() is not None


async def find(db: AsyncSession, model_run_id: str) -> dict[str, Any] | None:
    row = (
        (
            await db.execute(
                text(f"SELECT {_COLUMNS} FROM app_risk.model_run WHERE model_run_id = :id"),
                {"id": model_run_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    body = dict(row)
    body["drivers"] = _decode(body["drivers"])
    body["evidence_refs"] = _decode(body["evidence_refs"])
    body["reason_codes"] = list(body["reason_codes"] or [])
    return body


async def recent_for_member(db: AsyncSession, member_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                text(f"""
        SELECT {_COLUMNS} FROM app_risk.model_run
        WHERE member_id = :member_id ORDER BY created_at DESC LIMIT :limit
    """),
                {"member_id": member_id, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        body = dict(row)
        body["drivers"] = _decode(body["drivers"])
        body["evidence_refs"] = _decode(body["evidence_refs"])
        body["reason_codes"] = list(body["reason_codes"] or [])
        out.append(body)
    return out
