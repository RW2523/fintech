"""Persistence for execution-service."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.saga import Step

__all__ = [
    "claim_for_execution",
    "insert_action",
    "load_action",
    "record_failure",
    "record_steps",
    "record_success",
]


async def insert_action(db: AsyncSession, action: dict[str, Any]) -> dict[str, Any]:
    """Store a proposal. Storing the same one twice returns the first.

    Proposals arrive from the committee and from agents, and both may retry.
    An action id derived from what was proposed makes the retry land on the
    same row rather than creating a second request for the same document.
    """
    await db.execute(
        text("""
        INSERT INTO app_execution.action
          (action_id, case_id, member_id, decision_record_id, human_decision_id,
           level, type, parameters, rationale, requires, proposed_by, state)
        VALUES (:action_id, :case_id, :member_id, :decision_record_id, :human_decision_id,
                :level, :type, CAST(:parameters AS jsonb), CAST(:rationale AS jsonb),
                :requires, :proposed_by, 'PROPOSED')
        ON CONFLICT (action_id) DO NOTHING
    """),
        {
            **{
                k: action.get(k)
                for k in (
                    "action_id",
                    "case_id",
                    "member_id",
                    "decision_record_id",
                    "human_decision_id",
                    "level",
                    "type",
                    "requires",
                    "proposed_by",
                )
            },
            "parameters": json.dumps(action.get("parameters") or {}, default=str),
            "rationale": json.dumps(action.get("rationale") or {}, default=str),
        },
    )
    stored = await load_action(db, str(action["action_id"]))
    assert stored is not None
    return stored


async def load_action(db: AsyncSession, action_id: str) -> dict[str, Any] | None:
    row = (
        (
            await db.execute(
                text("SELECT * FROM app_execution.action WHERE action_id = :id"), {"id": action_id}
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    body = dict(row)
    for field in ("parameters", "rationale", "core_refs", "result"):
        if isinstance(body.get(field), str):
            body[field] = json.loads(body[field])
    return body


async def claim_for_execution(
    db: AsyncSession, action_id: str, *, token_id: str, idempotency_key: str
) -> dict[str, Any] | None:
    """Move a claimable action to EXECUTING, or return None.

    A single UPDATE with the state in its WHERE clause is what makes two
    concurrent executions safe: the second finds nothing to claim and reads
    the first's result instead of running the saga again.

    FAILED is claimable because a core that refused once must be retryable.
    The token is not required a second time: it was consumed on the first
    attempt, and the case has not been decided again since.
    """
    row = (
        (
            await db.execute(
                text("""
        UPDATE app_execution.action
           SET state = 'EXECUTING',
               token_id = COALESCE(token_id, :token_id),
               idempotency_key = COALESCE(idempotency_key, :key),
               attempts = attempts + 1
         WHERE action_id = :id AND state IN ('PROPOSED', 'FAILED')
        RETURNING *
    """),
                {"id": action_id, "token_id": token_id, "key": idempotency_key},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    body = dict(row)
    for field in ("parameters", "rationale", "core_refs", "result"):
        if isinstance(body.get(field), str):
            body[field] = json.loads(body[field])
    return body


async def record_steps(db: AsyncSession, action_id: str, attempt: int, steps: list[Step]) -> None:
    for step in steps:
        await db.execute(
            text("""
            INSERT INTO app_execution.saga_step
              (step_id, action_id, attempt, name, state, request, response, error, finished_at)
            VALUES (:step_id, :action_id, :attempt, :name, :state,
                    CAST(:request AS jsonb), CAST(:response AS jsonb), :error, now())
            ON CONFLICT (step_id) DO NOTHING
        """),
            {
                "step_id": step.step_id,
                "action_id": action_id,
                "attempt": attempt,
                "name": step.name,
                "state": step.state,
                "request": json.dumps(step.request, default=str),
                "response": json.dumps(step.response, default=str) if step.response else None,
                "error": step.error,
            },
        )


async def record_success(
    db: AsyncSession, action_id: str, *, core_refs: list[dict[str, Any]], result: dict[str, Any]
) -> None:
    await db.execute(
        text("""
        UPDATE app_execution.action
           SET state = 'EXECUTED', core_refs = CAST(:refs AS jsonb),
               result = CAST(:result AS jsonb), executed_at = now(), last_error = NULL
         WHERE action_id = :id
    """),
        {
            "id": action_id,
            "refs": json.dumps(core_refs, default=str),
            "result": json.dumps(result, default=str),
        },
    )


async def record_failure(db: AsyncSession, action_id: str, *, detail: str, state: str = "FAILED") -> None:
    """Leave the action where a retry can pick it up.

    The state is FAILED, not a terminal one: the case stays pending, which is
    what lets the workflow ask again rather than a person discovering an
    account that was opened and never approved.
    """
    await db.execute(
        text("""
        UPDATE app_execution.action SET state = :state, last_error = :detail WHERE action_id = :id
    """),
        {"id": action_id, "state": state, "detail": detail[:2000]},
    )
