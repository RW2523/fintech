"""Recording runs and opinions (docs/04 §4)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestrate import RunResult

__all__ = ["find_run", "list_opinions", "run_for_snapshot", "save_run"]

_RUN_COLUMNS = (
    "run_id",
    "snapshot_id",
    "case_id",
    "case_type",
    "tier",
    "tier_reasons",
    "state",
    "rounds",
    "repair_loops",
    "budgets",
    "decision_record_id",
    "timed_out",
    "detail",
    "started_at",
    "ended_at",
)
assert all(name.isidentifier() for name in _RUN_COLUMNS), _RUN_COLUMNS
_RUN = ", ".join(_RUN_COLUMNS)


def decode(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def run_for_snapshot(db: AsyncSession, snapshot_id: str, tier: str) -> dict[str, Any] | None:
    """The run this snapshot already has at this tier, if any.

    docs/06 §8 makes a run idempotent on the pair: re-submitting a case joins
    the run that exists rather than starting a second deliberation over the
    same frozen inputs.
    """
    row = (
        (
            await db.execute(
                text(
                    f"SELECT {_RUN} FROM app_committee.run WHERE snapshot_id = :snapshot_id AND tier = :tier"
                ),
                {"snapshot_id": snapshot_id, "tier": tier},
            )
        )
        .mappings()
        .one_or_none()
    )
    return _shape(row) if row else None


async def save_run(
    db: AsyncSession,
    result: RunResult,
    *,
    case_id: str | None = None,
    case_type: str = "ORIGINATION",
    tier_reasons: list[str] | None = None,
) -> bool:
    """Write the run and every opinion it produced."""
    written = await db.execute(
        text("""
        INSERT INTO app_committee.run
          (run_id, snapshot_id, case_id, case_type, tier, tier_reasons, state,
           rounds, repair_loops, budgets, decision_record_id, timed_out, detail,
           ended_at)
        VALUES (:run_id, :snapshot_id, :case_id, :case_type, :tier,
                :tier_reasons, :state, :rounds, :repair_loops,
                CAST(:budgets AS jsonb), :decision_record_id, :timed_out,
                :detail, now())
        ON CONFLICT (snapshot_id, tier) DO NOTHING
        RETURNING run_id
    """),
        {
            "run_id": result.run_id,
            "snapshot_id": result.snapshot_id,
            "case_id": case_id,
            "case_type": case_type,
            "tier": result.tier,
            "tier_reasons": list(tier_reasons or []),
            "state": result.state,
            "rounds": list(result.rounds),
            "repair_loops": result.repair_loops,
            "budgets": json.dumps(result.budgets),
            "decision_record_id": result.decision_record.get("decision_record_id"),
            "timed_out": result.timed_out,
            "detail": result.detail,
        },
    )
    first_time = written.scalar_one_or_none() is not None
    if not first_time:
        return False

    for entry in result.opinions:
        opinion = entry["opinion"]
        await db.execute(
            text("""
            INSERT INTO app_committee.opinion
              (opinion_id, run_id, agent_id, agent_version, round, stance,
               confidence, degraded, body, latency_ms)
            VALUES (:opinion_id, :run_id, :agent_id, :agent_version, :round,
                    :stance, :confidence, :degraded, CAST(:body AS jsonb),
                    :latency_ms)
            ON CONFLICT DO NOTHING
        """),
            {
                "opinion_id": opinion["opinion_id"],
                "run_id": result.run_id,
                "agent_id": opinion["agent_id"],
                "agent_version": opinion["agent_version"],
                "round": opinion["round"],
                "stance": opinion["stance"],
                "confidence": float(opinion["confidence"]),
                "degraded": bool(entry.get("degraded")),
                "body": json.dumps(opinion),
                "latency_ms": entry.get("latency_ms"),
            },
        )
    await db.commit()
    return True


def _shape(row: Any) -> dict[str, Any]:
    body = dict(row)
    body["budgets"] = decode(body["budgets"])
    body["rounds"] = list(body["rounds"] or [])
    body["tier_reasons"] = list(body["tier_reasons"] or [])
    return body


async def find_run(db: AsyncSession, run_id: str) -> dict[str, Any] | None:
    row = (
        (
            await db.execute(
                text(f"SELECT {_RUN} FROM app_committee.run WHERE run_id = :run_id"), {"run_id": run_id}
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    body = _shape(row)
    body["opinions"] = await list_opinions(db, run_id)
    return body


async def list_opinions(db: AsyncSession, run_id: str) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                text("""
        SELECT opinion_id, agent_id, agent_version, round, stance, confidence,
               degraded, body, latency_ms, created_at
          FROM app_committee.opinion WHERE run_id = :run_id
         ORDER BY created_at, agent_id
    """),
                {"run_id": run_id},
            )
        )
        .mappings()
        .all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        body = dict(row)
        body["body"] = decode(body["body"])
        out.append(body)
    return out
