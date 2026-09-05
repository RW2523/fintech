"""decision-service endpoints: the ledger, human decisions and tokens (docs/08 §6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from app import ledger, tokens
from app.authority import check_authority
from app.db import session
from app.models import HumanDecisionRequest, RecommendationRequest, TokenRequest
from app.settings import settings
from cio_common.errors import Conflict, NotFound, ValidationFailed
from cio_common.ids import new_id

router = APIRouter(tags=["decision"])


# ---------------------------------------------------------------------------
# recommendations
# ---------------------------------------------------------------------------
@router.post("/recommendations", summary="Append a DecisionRecord to the ledger")
async def append_recommendation(body: RecommendationRequest) -> dict[str, Any]:
    record = dict(body.decision_record)
    for required in ("decision_record_id", "snapshot_id", "recommendation", "route", "required_authority"):
        if required not in record:
            raise ValidationFailed(f"decision_record is missing {required!r}")

    async with session() as db:
        entry = await ledger.append(
            db, "DECISION_RECORD", record, case_id=body.case_id, member_id=body.member_id
        )

        # The ledger owns the chain, so the record's own link is set here.
        record["prev_hash"] = entry.prev_hash
        record["hash"] = entry.hash

        await db.execute(
            text("""
            INSERT INTO app_decision.decision_record
              (decision_record_id, case_id, snapshot_id, committee_run_id, recommendation,
               route, required_authority, body)
            VALUES (:id, :case_id, :snapshot_id, :run_id, :recommendation, :route,
                    :authority, CAST(:body AS jsonb))
            ON CONFLICT (decision_record_id) DO NOTHING
        """),
            {
                "id": record["decision_record_id"],
                "case_id": body.case_id,
                "snapshot_id": record["snapshot_id"],
                "run_id": record.get("committee_run_id"),
                "recommendation": record["recommendation"],
                "route": record["route"],
                "authority": record["required_authority"],
                "body": json.dumps(record, default=str),
            },
        )

    return {
        "decision_record_id": record["decision_record_id"],
        "entry_id": entry.entry_id,
        "seq": entry.seq,
        "hash": entry.hash,
        "prev_hash": entry.prev_hash,
    }


@router.get("/decision-records/{record_id}", summary="One decision record")
async def get_record(record_id: str) -> dict[str, Any]:
    async with session() as db:
        row = (
            (
                await db.execute(
                    text("""
            SELECT body, superseded_by FROM app_decision.decision_record
            WHERE decision_record_id = :id
        """),
                    {"id": record_id},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise NotFound(f"no decision record {record_id!r}")
    body = row["body"]
    record = json.loads(body) if isinstance(body, str) else body
    return {**record, "superseded_by": row["superseded_by"]}


# ---------------------------------------------------------------------------
# human decisions
# ---------------------------------------------------------------------------
@router.post("/human-decisions", summary="Record a person's decision on a case")
async def record_human_decision(body: HumanDecisionRequest) -> dict[str, Any]:
    """Authority is re-checked here, whatever the UI allowed (docs/13 §1)."""
    async with session() as db:
        row = (
            (
                await db.execute(
                    text("""
            SELECT required_authority, superseded_by FROM app_decision.decision_record
            WHERE decision_record_id = :id
        """),
                    {"id": body.decision_record_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound(f"no decision record {body.decision_record_id!r}")
        if row["superseded_by"]:
            raise Conflict("this decision record has been superseded", superseded_by=row["superseded_by"])

        authority = check_authority(body.role, row["required_authority"], body.final_action)

        decision: dict[str, Any] = {
            "schema": "human_decision/1.0",
            "human_decision_id": new_id("hd"),
            "decision_record_id": body.decision_record_id,
            "case_id": body.case_id,
            "actor_id": body.actor_id,
            "authority_role": authority,
            "final_action": body.final_action,
            "conditions": body.conditions,
            "override": body.override,
            "evidence_acknowledged": body.evidence_acknowledged,
            "decided_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        if body.override_reason is not None:
            decision["override_reason"] = body.override_reason.model_dump()

        entry = await ledger.append(db, "HUMAN_DECISION", decision, case_id=body.case_id)
        decision["prev_hash"] = entry.prev_hash
        decision["hash"] = entry.hash

        await db.execute(
            text("""
            INSERT INTO app_decision.human_decision
              (human_decision_id, decision_record_id, case_id, actor_id, authority_role,
               final_action, override, body)
            VALUES (:id, :record_id, :case_id, :actor_id, :authority, :action,
                    :override, CAST(:body AS jsonb))
        """),
            {
                "id": decision["human_decision_id"],
                "record_id": body.decision_record_id,
                "case_id": body.case_id,
                "actor_id": body.actor_id,
                "authority": authority,
                "action": body.final_action,
                "override": body.override,
                "body": json.dumps(decision, default=str),
            },
        )

    return decision


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------
@router.get("/ledger", summary="The chain, or one case's slice of it")
async def get_ledger(
    case_id: str | None = None,
    limit: int = Query(default=1000, ge=1, le=10000),
) -> dict[str, Any]:
    async with session() as db:
        entries = await ledger.chain_for(db, case_id=case_id, limit=limit)
    return {"case_id": case_id, "entries": [e.as_dict() for e in entries], "count": len(entries)}


@router.get("/ledger/verify", summary="Recompute the hash chain")
async def verify_ledger(
    from_seq: int = Query(default=0, ge=0),
    to_seq: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    """docs/14 §4 — green on an intact chain, red on a tampered one."""
    async with session() as db:
        intact, breaks, checked = await ledger.verify(db, from_seq=from_seq, to_seq=to_seq)
    return {
        "intact": intact,
        "entries_checked": checked,
        "breaks": [b.as_dict() for b in breaks],
        "verified_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------
@router.post("/tokens", summary="Issue an approval token")
async def issue_token(body: TokenRequest) -> dict[str, Any]:
    issued_to = body.issued_to or {"actor_id": "workflow", "kind": "WORKFLOW"}
    async with session() as db:
        token = await tokens.issue(
            db,
            secret=settings().token_secret,
            action_id=body.action_id,
            decision_record_id=body.decision_record_id,
            issued_to=issued_to,
            product_code=body.product_code,
            member_id=body.member_id,
            case_id=body.case_id,
            max_amount=body.max_amount,
            idempotency_key=body.idempotency_key,
            human_decision_id=body.human_decision_id,
            ttl=timedelta(seconds=body.ttl_seconds),
        )
        await ledger.append(
            db,
            "TOKEN",
            {k: v for k, v in token.items() if k != "signature"},
            case_id=body.case_id,
            member_id=body.member_id,
        )
    return token


@router.get("/tokens/{token_id}/validate", summary="Check a token without using it")
async def validate_token(token_id: str, case_id: str | None = None) -> dict[str, Any]:
    async with session() as db:
        token = await tokens.validate(db, secret=settings().token_secret, token_id=token_id, case_id=case_id)
    return {
        "valid": True,
        "token_id": token["token_id"],
        "scope": token["scope"],
        "expires_at": token["expires_at"],
    }


@router.post("/tokens/{token_id}/redeem", summary="Consume a token (execution only)")
async def redeem_token(token_id: str, case_id: str | None = None) -> dict[str, Any]:
    async with session() as db:
        token = await tokens.redeem(db, secret=settings().token_secret, token_id=token_id, case_id=case_id)
    return {"redeemed": True, "token_id": token["token_id"], "scope": token["scope"]}


# ---------------------------------------------------------------------------
# generic append, used by services that own no ledger tables
# ---------------------------------------------------------------------------
@router.post("/ledger/entries", summary="Append an entry of any documented kind")
async def append_entry(body: dict[str, Any]) -> dict[str, Any]:
    kind = body.get("kind")
    if kind not in ledger_kinds():
        raise ValidationFailed(f"unknown ledger kind {kind!r}", kinds=sorted(ledger_kinds()))
    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise ValidationFailed("payload must be an object")

    async with session() as db:
        entry = await ledger.append(
            db, kind, payload, case_id=body.get("case_id"), member_id=body.get("member_id")
        )
    return entry.as_dict()


def ledger_kinds() -> set[str]:
    from app.schema import ENTRY_KINDS

    return set(ENTRY_KINDS)
