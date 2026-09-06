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
    "active_amendment",
    "amendment_history",
    "freeze_inputs",
    "freeze_outcome",
    "kill_switch_active",
    "kill_switch_state",
    "load_replay_cases",
    "next_amendment_sequence",
    "save_amendment",
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


# ---------------------------------------------------------------------------
# the Autonomy Dial (docs/05 §6)
# ---------------------------------------------------------------------------
async def active_amendment(db: AsyncSession, product_code: str) -> dict[str, Any] | None:
    """The dial setting in force, or None when the pack's own stands.

    Newest first and only one active: a change supersedes rather than layers,
    so there is never a question of which of two amendments applies.
    """
    row = (
        (
            await db.execute(
                text("""
        SELECT version, body, approved_by, approved_at, effective_from
          FROM app_policy.policy_version
         WHERE product_code = :p AND kind = 'autonomy' AND status = 'ACTIVE'
         ORDER BY approved_at DESC
         LIMIT 1
    """),
                {"p": product_code},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return {
        "version": row["version"],
        "body": _json(row["body"]),
        "approved_by": list(row["approved_by"] or []),
        "approved_at": row["approved_at"],
        "effective_from": row["effective_from"],
    }


async def amendment_history(db: AsyncSession, product_code: str, limit: int = 50) -> list[dict[str, Any]]:
    """Every dial change on this product, newest first.

    A superseded setting is kept, not deleted: a decision made last Tuesday was
    made under whatever the dial read last Tuesday, and an auditor asking what
    that was deserves an answer.
    """
    rows = (
        (
            await db.execute(
                text("""
        SELECT version, body, approved_by, approved_at, status
          FROM app_policy.policy_version
         WHERE product_code = :p AND kind = 'autonomy'
         ORDER BY approved_at DESC NULLS LAST
         LIMIT :limit
    """),
                {"p": product_code, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    return [
        {
            "version": row["version"],
            "setting": _json(row["body"]).get("setting"),
            "approved_by": list(row["approved_by"] or []),
            "approved_at": row["approved_at"],
            "status": row["status"],
        }
        for row in rows
    ]


async def save_amendment(
    db: AsyncSession,
    *,
    product_code: str,
    version: str,
    body: dict[str, Any],
    approved_by: list[str],
) -> None:
    """Record a dial change and retire the one it replaces, in one statement
    each so a failure cannot leave two active."""
    await db.execute(
        text("""
        UPDATE app_policy.policy_version
           SET status = 'SUPERSEDED'
         WHERE product_code = :p AND kind = 'autonomy' AND status = 'ACTIVE'
    """),
        {"p": product_code},
    )
    await db.execute(
        text("""
        INSERT INTO app_policy.policy_version
          (version, product_code, kind, body, approved_by, approved_at, effective_from, status)
        VALUES (:version, :p, 'autonomy', :body, :approved_by, now(), now(), 'ACTIVE')
    """),
        {
            "version": version,
            "p": product_code,
            "body": json.dumps(body),
            "approved_by": approved_by,
        },
    )


async def next_amendment_sequence(db: AsyncSession, product_code: str) -> int:
    count = (
        await db.execute(
            text("""
        SELECT count(*) FROM app_policy.policy_version
         WHERE product_code = :p AND kind = 'autonomy'
    """),
            {"p": product_code},
        )
    ).scalar_one()
    return int(count) + 1


async def kill_switch_state(db: AsyncSession, product_code: str) -> dict[str, Any]:
    """Whether the switch is on, and who pulled it when."""
    row = (
        (
            await db.execute(
                text("""
        SELECT enabled, activated_by, activated_at, reason
          FROM app_policy.kill_switch WHERE product_code = :p
    """),
                {"p": product_code},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return {"enabled": False, "activated_by": None, "activated_at": None, "reason": None}
    return {
        "enabled": bool(row["enabled"]),
        "activated_by": row["activated_by"],
        "activated_at": row["activated_at"],
        "reason": row["reason"],
    }


async def load_sandbox_run(db: AsyncSession, sandbox_id: str) -> dict[str, Any] | None:
    """One sandbox run, so an adoption can be tied to the replay behind it."""
    row = (
        (
            await db.execute(
                text("""
        SELECT sandbox_id, product_code, candidate, range, results, created_at
          FROM app_policy.sandbox_run WHERE sandbox_id = :sandbox_id
    """),
                {"sandbox_id": sandbox_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def freeze_inputs(
    db: AsyncSession,
    *,
    snapshot_id: str,
    product_code: str,
    policy_version: str,
    inputs: dict[str, Any],
) -> None:
    """Half of a replay case: what the gates were run over.

    Written at evaluation because that is the only point in the live path
    where the raw inputs exist. `save_replay_case` above wants both halves at
    once and nothing in the platform ever had them together, which is why the
    replay table only ever held rows a fixture had put there: the sandbox
    could replay seeded cases and never a case the platform had actually
    decided.
    """
    await db.execute(
        text("""
        INSERT INTO app_policy.replay_case
          (snapshot_id, product_code, policy_version, decided_at, inputs,
           factor_scores, opinions, baseline, segment)
        VALUES (:snapshot_id, :product_code, :policy_version, now(),
                CAST(:inputs AS jsonb), '[]'::jsonb, '[]'::jsonb, '{}'::jsonb, '{}'::jsonb)
        ON CONFLICT (snapshot_id) DO UPDATE SET
          inputs = EXCLUDED.inputs,
          policy_version = EXCLUDED.policy_version
    """),
        {
            "snapshot_id": snapshot_id,
            "product_code": product_code,
            "policy_version": policy_version,
            "inputs": json.dumps(inputs, default=str),
        },
    )


async def freeze_outcome(
    db: AsyncSession,
    *,
    snapshot_id: str,
    case_id: str | None,
    factor_scores: list[dict[str, Any]],
    opinions: list[dict[str, Any]],
    baseline: dict[str, Any],
    segment: dict[str, str] | None = None,
    pd_12m: float | None = None,
) -> bool:
    """The other half: what was decided, and by what.

    Returns False when no inputs were frozen for this snapshot. That is not an
    error: a caller may synthesize without having evaluated through this
    service, and a replay case with no inputs cannot be re-decided, so the row
    is left alone rather than half-written.
    """
    result = await db.execute(
        text("""
        UPDATE app_policy.replay_case
           SET case_id = COALESCE(:case_id, case_id),
               factor_scores = CAST(:factor_scores AS jsonb),
               opinions = CAST(:opinions AS jsonb),
               baseline = CAST(:baseline AS jsonb),
               segment = CAST(:segment AS jsonb),
               pd_12m = COALESCE(:pd_12m, pd_12m),
               decided_at = now()
         WHERE snapshot_id = :snapshot_id
     RETURNING snapshot_id
    """),
        {
            "snapshot_id": snapshot_id,
            "case_id": case_id,
            "factor_scores": json.dumps(factor_scores, default=str),
            "opinions": json.dumps(opinions, default=str),
            "baseline": json.dumps(baseline, default=str),
            "segment": json.dumps(segment or {}),
            "pd_12m": pd_12m,
        },
    )
    return result.first() is not None
