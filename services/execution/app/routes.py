"""execution-service endpoints (docs/08 §7).

This is the only service that changes anything outside the platform. Everything
before it produced a recommendation, a decision or an approval; this turns one
into a facility on a member's record. So the checks here are not a formality:
they are the last place a mistake can still be cheap.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter

from app import repository
from app.clients import Clients, CoreRefusedError, HttpClients, TokenRefusedError, UpstreamError
from app.db import session
from app.models import ActionProposalRequest, ExecuteRequest
from app.saga import SagaFailureError, run_saga
from app.settings import settings
from cio_common.errors import Conflict, Forbidden, KillSwitchActive, NotFound, TokenInvalid
from cio_common.outbox import emit

router = APIRouter(tags=["execution"])

_clients: Clients | None = None


def clients() -> Clients:
    global _clients
    if _clients is None:
        _clients = HttpClients()
    return _clients


def set_clients(replacement: Clients | None) -> None:
    global _clients
    _clients = replacement


@router.post("/action-proposals", summary="Record a proposed action")
async def propose(body: ActionProposalRequest) -> dict[str, Any]:
    """A proposal is recorded, never carried out. Nothing here executes."""
    async with session() as db:
        stored = await repository.insert_action(db, body.model_dump())
        await db.commit()
    return {
        "action_id": stored["action_id"],
        "state": stored["state"],
        "level": stored["level"],
        "type": stored["type"],
    }


@router.get("/actions/{action_id}", summary="One action and what happened to it")
async def get_action(action_id: str) -> dict[str, Any]:
    async with session() as db:
        action = await repository.load_action(db, action_id)
    if action is None:
        raise NotFound(f"no action {action_id!r}")
    return _public(action)


@router.post("/actions/{action_id}/execute", summary="Carry out an approved action")
async def execute(action_id: str, body: ExecuteRequest) -> dict[str, Any]:
    """docs/08 §7 — token, scope, kill switch, then the saga.

    The order matters. The kill switch is checked before the token is consumed,
    so a stop does not burn an approval that a person will have to issue again.
    """
    async with session() as db:
        action = await repository.load_action(db, action_id)
    if action is None:
        raise NotFound(f"no action {action_id!r}")

    # An action already carried out is not carried out again. The stored
    # result is returned instead, because the caller asking twice usually means
    # they did not hear the first answer, not that they want it done twice.
    if action["state"] == "EXECUTED":
        return {**_public(action), "replayed": True}
    if action["state"] == "EXECUTING":
        raise Conflict(f"action {action_id!r} is already being executed", state=action["state"])

    if await clients().kill_switch_active(body.product_code):
        raise KillSwitchActive(
            f"the kill switch is on for {body.product_code}; nothing is executed",
            product_code=body.product_code,
            action_id=action_id,
        )

    # A retry after a core failure does not need a second token: the first was
    # consumed, and the case has not been decided again since. Asking for a new
    # one would mean a person re-approving a decision they already approved
    # because a core write timed out.
    first_attempt = action["token_id"] is None
    if first_attempt:
        try:
            await clients().validate_token(body.token_id, case_id=action.get("case_id") or None)
            await clients().redeem_token(body.token_id, case_id=action.get("case_id") or None)
        except TokenRefusedError as exc:
            raise TokenInvalid(str(exc), **(exc.detail or {})) from exc
    elif action["token_id"] != body.token_id:
        # A different token on a retry would let a second approval be spent on
        # an action that already has one.
        raise Forbidden(
            f"action {action_id!r} was approved by {action['token_id']!r}",
            token_id=action["token_id"],
        )

    key = body.idempotency_key or f"exec-{action_id}"
    async with session() as db:
        claimed = await repository.claim_for_execution(
            db, action_id, token_id=body.token_id, idempotency_key=key
        )
        await db.commit()
    if claimed is None:
        # Somebody else claimed it between the read and the update.
        async with session() as db:
            current = await repository.load_action(db, action_id)
        raise Conflict(
            f"action {action_id!r} was claimed by another execution",
            state=(current or {}).get("state"),
        )

    before = _core_state(claimed)
    try:
        saga = await run_saga(clients(), claimed, body.token_id)
    except SagaFailureError as exc:
        async with session() as db:
            await repository.record_steps(db, action_id, int(claimed["attempts"]), exc.result.steps)
            await repository.record_failure(
                db,
                action_id,
                detail=exc.result.detail or str(exc),
                state="REFUSED" if exc.result.state == "REFUSED" else "FAILED",
            )
            await _audit(db, claimed, before, exc.result.state, exc.result.detail)
            await db.commit()

        if exc.result.state == "REFUSED":
            raise Forbidden(str(exc), action_id=action_id) from exc
        # The case stays pending. A failure that is recorded and retryable is
        # recoverable; a write that half-happened and was reported as done is
        # not.
        raise Conflict(
            f"the core refused: {exc.result.detail}",
            action_id=action_id,
            state="FAILED",
            compensated=exc.result.compensated,
            retryable=True,
        ) from exc
    except (CoreRefusedError, UpstreamError) as exc:  # pragma: no cover - defensive
        async with session() as db:
            await repository.record_failure(db, action_id, detail=str(exc))
            await db.commit()
        raise Conflict(str(exc), action_id=action_id, retryable=True) from exc

    result = {"state": "EXECUTED", "core_refs": saga.core_refs}
    async with session() as db:
        await repository.record_steps(db, action_id, int(claimed["attempts"]), saga.steps)
        await repository.record_success(db, action_id, core_refs=saga.core_refs, result=result)
        await _audit(db, claimed, before, "EXECUTED", None, core_refs=saga.core_refs)
        await db.commit()
        executed = await repository.load_action(db, action_id)

    return {**_public(executed or claimed), "replayed": False}


async def _audit(
    db: Any,
    action: dict[str, Any],
    before: dict[str, Any],
    outcome: str,
    detail: str | None,
    core_refs: list[dict[str, Any]] | None = None,
) -> None:
    """docs/08 §7 — before and after, on every attempt.

    Emitted through the outbox rather than written straight to an audit table:
    the audit service owns that table and arrives with T-053. An event in the
    outbox is durable and ordered, so nothing is lost in the meantime.
    """
    await emit(
        db,
        "action.executed" if outcome == "EXECUTED" else "action.failed",
        {
            "action_id": action["action_id"],
            "type": action["type"],
            "level": action["level"],
            "case_id": action.get("case_id"),
            "member_id": action.get("member_id"),
            "decision_record_id": action.get("decision_record_id"),
            "token_id": action.get("token_id"),
            "attempt": action.get("attempts"),
            "before": before,
            "after": {"state": outcome, "core_refs": core_refs or []},
            "detail": detail,
        },
        key=str(action["action_id"]),
        producer="execution",
        case_id=action.get("case_id"),
    )


def _core_state(action: dict[str, Any]) -> dict[str, Any]:
    """What the platform believed before it wrote anything."""
    return {
        "state": action.get("state"),
        "core_refs": action.get("core_refs") or [],
        "attempts": action.get("attempts"),
    }


def _public(action: dict[str, Any]) -> dict[str, Any]:
    refs = action.get("core_refs") or []
    if isinstance(refs, str):
        refs = json.loads(refs)
    return {
        "action_id": action["action_id"],
        "state": action["state"],
        "type": action["type"],
        "level": action["level"],
        "case_id": action.get("case_id"),
        "core_refs": refs,
        "attempts": action.get("attempts", 0),
        "last_error": action.get("last_error"),
        "executed_at": action.get("executed_at"),
    }


__all__ = ["clients", "router", "set_clients", "settings"]
