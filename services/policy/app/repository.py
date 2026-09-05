"""Persistence for policy-service (docs/04 §4)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.context import PolicyInputs
from app.factors import FactorScore
from app.sandbox import ReplayCase

__all__ = [
    "kill_switch_active",
    "load_replay_cases",
    "save_replay_case",
    "save_sandbox_run",
    "set_kill_switch",
]


#: Fields stored as strings that must come back as Decimal (CLAUDE.md §7).
_MONEY_FIELDS = frozenset(
    {
        "member_total_exposure",
        "requested_amount",
        "income_verified_monthly",
        "commitments_monthly",
        "actor_max_amount",
    }
)


def _to_inputs(body: dict[str, Any]) -> PolicyInputs:
    """Rebuild the frozen inputs of a decided case."""
    converted: dict[str, Any] = {}
    for key, value in body.items():
        if key in _MONEY_FIELDS and value is not None:
            converted[key] = Decimal(str(value))
        else:
            converted[key] = value

    present: Any = converted.get("documents_present") or ()
    converted["documents_present"] = tuple(present)
    return PolicyInputs(**converted)


async def save_replay_case(
    db: AsyncSession,
    *,
    snapshot_id: str,
    product_code: str,
    policy_version: str,
    inputs: dict[str, Any],
    factor_scores: list[dict[str, Any]],
    baseline: dict[str, Any],
    opinions: list[dict[str, Any]] | None = None,
    segment: dict[str, str] | None = None,
    pd_12m: float | None = None,
    case_id: str | None = None,
    decided_at: datetime | None = None,
) -> None:
    """Freeze a decided case so the sandbox can replay it."""
    await db.execute(
        text("""
        INSERT INTO app_policy.replay_case
          (snapshot_id, case_id, product_code, policy_version, decided_at, inputs,
           factor_scores, opinions, baseline, segment, pd_12m)
        VALUES (:snapshot_id, :case_id, :product_code, :policy_version, :decided_at,
                CAST(:inputs AS jsonb), CAST(:factor_scores AS jsonb),
                CAST(:opinions AS jsonb), CAST(:baseline AS jsonb),
                CAST(:segment AS jsonb), :pd_12m)
        ON CONFLICT (snapshot_id) DO UPDATE SET
          baseline = EXCLUDED.baseline, factor_scores = EXCLUDED.factor_scores
    """),
        {
            "snapshot_id": snapshot_id,
            "case_id": case_id,
            "product_code": product_code,
            "policy_version": policy_version,
            "decided_at": decided_at or datetime.now(UTC),
            "inputs": json.dumps(inputs, default=str),
            "factor_scores": json.dumps(factor_scores),
            "opinions": json.dumps(opinions or []),
            "baseline": json.dumps(baseline, default=str),
            "segment": json.dumps(segment or {}),
            "pd_12m": pd_12m,
        },
    )


async def load_replay_cases(
    db: AsyncSession,
    *,
    product_code: str,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    snapshot_ids: list[str] | None = None,
    limit: int = 5000,
) -> list[ReplayCase]:
    """Cases to replay, by explicit id or by decision date."""
    if snapshot_ids:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT * FROM app_policy.replay_case
            WHERE snapshot_id = ANY(:ids) ORDER BY decided_at LIMIT :limit
        """),
                    {"ids": snapshot_ids, "limit": limit},
                )
            )
            .mappings()
            .all()
        )
    else:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT * FROM app_policy.replay_case
            WHERE product_code = :product
              -- the casts let asyncpg infer a type when the bound value is NULL
              AND (CAST(:date_from AS timestamptz) IS NULL
                   OR decided_at >= CAST(:date_from AS timestamptz))
              AND (CAST(:date_to AS timestamptz) IS NULL
                   OR decided_at <= CAST(:date_to AS timestamptz))
            ORDER BY decided_at LIMIT :limit
        """),
                    {"product": product_code, "date_from": date_from, "date_to": date_to, "limit": limit},
                )
            )
            .mappings()
            .all()
        )

    cases: list[ReplayCase] = []
    for row in rows:
        factor_scores = tuple(
            FactorScore(
                family=f["family"],
                score=int(f["score"]),
                calc_id=f["calc_id"],
                tool=f.get("tool", ""),
                inputs_digest=f.get("inputs_digest", "0" * 64),
                evidence_refs=tuple(f.get("evidence_refs", [])),
                level=f.get("level"),
            )
            for f in _json(row["factor_scores"])
        )
        cases.append(
            ReplayCase(
                snapshot_id=row["snapshot_id"],
                case_id=row["case_id"],
                product_code=row["product_code"],
                inputs=_to_inputs(_json(row["inputs"])),
                factor_scores=factor_scores,
                opinions=tuple(_json(row["opinions"])),
                baseline=_json(row["baseline"]),
                segment=_json(row["segment"]),
                pd_12m=row["pd_12m"],
            )
        )
    return cases


async def save_sandbox_run(
    db: AsyncSession,
    *,
    sandbox_id: str,
    product_code: str,
    candidate: dict[str, Any],
    case_range: dict[str, Any],
    results: dict[str, Any],
    created_by: str | None = None,
) -> None:
    await db.execute(
        text("""
        INSERT INTO app_policy.sandbox_run
          (sandbox_id, product_code, candidate, range, results, created_by)
        VALUES (:sandbox_id, :product_code, CAST(:candidate AS jsonb),
                CAST(:range AS jsonb), CAST(:results AS jsonb), :created_by)
    """),
        {
            "sandbox_id": sandbox_id,
            "product_code": product_code,
            "candidate": json.dumps(candidate),
            "range": json.dumps(case_range, default=str),
            "results": json.dumps(results, default=str),
            "created_by": created_by,
        },
    )


async def kill_switch_active(db: AsyncSession, product_code: str) -> bool:
    row = (
        await db.execute(
            text("SELECT enabled FROM app_policy.kill_switch WHERE product_code = :p"), {"p": product_code}
        )
    ).scalar_one_or_none()
    return bool(row)


async def set_kill_switch(
    db: AsyncSession, product_code: str, *, enabled: bool, actor: str, reason: str | None
) -> None:
    await db.execute(
        text("""
        INSERT INTO app_policy.kill_switch
          (product_code, enabled, activated_by, activated_at, reason)
        VALUES (:p, :enabled, :actor, now(), :reason)
        ON CONFLICT (product_code) DO UPDATE SET
          enabled = EXCLUDED.enabled, activated_by = EXCLUDED.activated_by,
          activated_at = now(), reason = EXCLUDED.reason
    """),
        {"p": product_code, "enabled": enabled, "actor": actor, "reason": reason},
    )


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value
