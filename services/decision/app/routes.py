"""decision-service endpoints: the ledger, human decisions and tokens (docs/08 §6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import text

from app import ledger, tokens
from app.authority import check_authority
from app.db import session
from app.models import (
    HumanDecisionRequest,
    RecommendationRequest,
    SampleReviewRequest,
    TokenRequest,
)
from app.settings import settings
from cio_common.auth import ROLES
from cio_common.errors import Conflict, Forbidden, NotFound, ValidationFailed
from cio_common.ids import derived_id, new_id
from cio_common.metrics import decision_queue_depth, ledger_entries
from cio_common.outbox import emit

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

        # docs/05 §6 — a sampled autonomous decision joins the review queue as
        # it is recorded, in the same transaction. A queue written afterwards
        # can be missed, and the one decision nobody looked at would be the one
        # the platform made on its own.
        sample = await _queue_sample(db, record, body.sampling)

        await emit(
            db,
            "decision.recorded",
            {
                "decision_record_id": record["decision_record_id"],
                "case_id": body.case_id,
                "committee_run_id": record.get("committee_run_id"),
                "before": None,
                "after": {
                    "recommendation": record["recommendation"],
                    "route": record["route"],
                    "sampled": bool(record.get("sampled")),
                },
                "policy_version": record.get("policy_version"),
                "model_versions": record.get("model_versions") or {},
            },
            key=str(record["decision_record_id"]),
            producer="decision",
            case_id=body.case_id,
        )

    return {
        "decision_record_id": record["decision_record_id"],
        "entry_id": entry.entry_id,
        "seq": entry.seq,
        "hash": entry.hash,
        "prev_hash": entry.prev_hash,
        "sample_id": sample,
    }


async def _queue_sample(db: Any, record: dict[str, Any], sampling: dict[str, Any] | None) -> str | None:
    """Put a sampled decision in front of a reviewer, or return None.

    Only a decision the platform made alone is sampled: a case a person already
    decided has been reviewed by definition, and putting it in the queue would
    dilute the sample with cases nobody needs to look at again.
    """
    if record.get("route") != "AUTONOMOUS" or not record.get("sampled"):
        return None

    settings = sampling or {}
    role = str(settings.get("reviewer_role") or "SENIOR_OFFICER")
    hours = int(settings.get("sla_hours") or 24)
    # Derived from the record, so replaying the same decision does not queue a
    # second review of it.
    sample_id = derived_id("smp", str(record["decision_record_id"]))

    await db.execute(
        text("""
        INSERT INTO ledger.sample_review
          (sample_id, decision_record_id, assigned_role, due_at)
        VALUES (:sample_id, :record_id, :role, now() + make_interval(hours => :hours))
        ON CONFLICT (sample_id) DO NOTHING
    """),
        {
            "sample_id": sample_id,
            "record_id": record["decision_record_id"],
            "role": role,
            "hours": hours,
        },
    )
    return sample_id


#: docs/09 §2 — the officer queue is sorted by how much attention a case needs,
#: not by when it arrived. A compliance review left for later is a worse
#: outcome than an officer review left for later.
ROUTE_PRIORITY = (
    "COMPLIANCE_REVIEW",
    "COMPLIANCE",
    "ENHANCED_ASSESSMENT",
    "COMMITTEE",
    "SENIOR_REVIEW",
    "OFFICER_REVIEW",
    "MANUAL_FALLBACK",
    "AUTONOMOUS",
)


@router.get("/queue", summary="Decisions waiting for a person")
async def queue(route: str | None = None, limit: int = 100) -> dict[str, Any]:
    """Every recorded decision that still needs someone, worst first.

    Read from the ledger rather than from a work table, because the ledger is
    the record: a queue built beside it could disagree with what was decided.
    """
    async with session() as db:
        rows = await ledger.decision_records(db, limit=min(limit, 500))

    order = {name: index for index, name in enumerate(ROUTE_PRIORITY)}
    entries = []
    for row in rows:
        record = row["payload"]
        if route and str(record.get("route")) != route:
            continue
        entries.append(
            {
                "decision_record_id": record.get("decision_record_id"),
                "case_id": row.get("case_id"),
                "snapshot_id": record.get("snapshot_id"),
                "member_id": row.get("member_id"),
                "tier": record.get("tier"),
                "route": record.get("route"),
                "route_reasons": record.get("route_reasons") or [],
                "recommendation": record.get("recommendation"),
                "confidence": record.get("confidence"),
                "disagreement": record.get("disagreement"),
                "weighted_score": record.get("weighted_score"),
                "required_authority": record.get("required_authority"),
                "challenger_open": record.get("challenger_open"),
                "created_at": row.get("created_at"),
                "decided": row.get("decided", False),
            }
        )

    entries.sort(key=lambda e: (order.get(str(e["route"]), len(order)), str(e["created_at"])))
    # Set on the read rather than on the write. The queue is derived from the
    # ledger, so the only honest moment to measure its depth is when it has
    # just been derived; a counter incremented on append would drift the first
    # time a decision was superseded.
    waiting: dict[str, int] = dict.fromkeys(ROUTE_PRIORITY, 0)
    for entry in entries:
        if not entry.get("decided"):
            waiting[str(entry.get("route") or "UNKNOWN")] = (
                waiting.get(str(entry.get("route") or "UNKNOWN"), 0) + 1
            )
    for name, depth in waiting.items():
        decision_queue_depth.labels(route=name).set(depth)
    ledger_entries.labels(chain="ledger").set(len(rows))

    return {"count": len(entries), "routes": list(ROUTE_PRIORITY), "decisions": entries}


#: Who may read any sample, not only their own. These are the roles that own
#: the Autonomy Dial (docs/05 §6): the people accountable for the programme
#: are the people who may look at what it did.
SAMPLE_OVERSIGHT = frozenset({"HEAD_OF_CREDIT", "HEAD_OF_RISK"})


# ---------------------------------------------------------------------------
# the sampling queue (docs/05 §6, docs/08 §6)
#
# A share of what the platform decides alone is read by a person afterwards.
# The point is not to catch a bad decision, though it might: it is to keep a
# human eye on what autonomy is actually doing, at a rate the institution set.
# ---------------------------------------------------------------------------
@router.get("/samples", summary="Autonomous decisions waiting to be reviewed")
async def samples(role: str | None = None, reviewed: bool = False, limit: int = 100) -> dict[str, Any]:
    """Oldest deadline first: a sample review has an SLA, and a queue sorted by
    arrival buries the one about to breach it."""
    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT s.sample_id, s.decision_record_id, s.assigned_role, s.due_at,
                   s.reviewed_by, s.verdict, s.notes, s.created_at,
                   r.case_id, r.recommendation, r.route
              FROM ledger.sample_review s
              LEFT JOIN app_decision.decision_record r
                     ON r.decision_record_id = s.decision_record_id
             WHERE (CAST(:role AS text) IS NULL OR s.assigned_role = CAST(:role AS text))
               AND (s.verdict IS NOT NULL) = CAST(:reviewed AS boolean)
             ORDER BY s.due_at ASC
             LIMIT :limit
        """),
                    {"role": role, "reviewed": reviewed, "limit": limit},
                )
            )
            .mappings()
            .all()
        )

    now = datetime.now(UTC)
    return {
        "count": len(rows),
        "samples": [
            {
                **dict(row),
                # Stated rather than left to the reader to work out from a
                # timestamp, because whether it is late is the thing that
                # decides what they do next.
                "overdue": row["verdict"] is None and row["due_at"] < now,
            }
            for row in rows
        ],
    }


@router.post("/samples/{sample_id}/review", summary="Record a sample review")
async def review_sample(sample_id: str, body: SampleReviewRequest, request: Request) -> dict[str, Any]:
    """A verdict is written once. Reviewing again would let a disagreement be
    quietly replaced by an agreement."""
    async with session() as db:
        row = (
            (
                await db.execute(
                    text("""
            SELECT decision_record_id, assigned_role, verdict
              FROM ledger.sample_review WHERE sample_id = :id
        """),
                    {"id": sample_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound(f"no sample {sample_id!r}")
        if row["verdict"] is not None:
            raise Conflict(
                f"sample {sample_id!r} was already reviewed",
                verdict=row["verdict"],
            )

        # The header carries the sign-in role; the queue carries the authority
        # the pack asked for. Comparing them directly rejects the very person
        # the sample was assigned to, so the role is mapped to its authority
        # first.
        #
        # The approval ladder is deliberately not used here. It orders who may
        # approve how much financing, which is a different question from who
        # may read a sample, and borrowing it would give an oversight check the
        # meaning of an approval right. The two heads who own the autonomy
        # programme may read any sample in it; everyone else reads their own.
        role = (request.headers.get("x-principal-role") or "").lower()
        assigned = str(row["assigned_role"]).upper()
        if role:
            authority = ROLES.get(role)
            if authority is None or not (authority == assigned or authority in SAMPLE_OVERSIGHT):
                raise Forbidden(
                    f"sample {sample_id!r} is assigned to {row['assigned_role']}",
                    assigned_role=row["assigned_role"],
                    actor_authority=authority,
                )

        await db.execute(
            text("""
            UPDATE ledger.sample_review
               SET reviewed_by = :reviewer, verdict = :verdict, notes = :notes
             WHERE sample_id = :id
        """),
            {
                "id": sample_id,
                "reviewer": body.reviewer_id,
                "verdict": body.verdict,
                "notes": body.notes,
            },
        )

        # The verdict joins the chain: a review that disagreed with an
        # autonomous decision is part of that decision's history.
        entry = await ledger.append(
            db,
            "SAMPLE_REVIEW",
            {
                "sample_id": sample_id,
                "decision_record_id": row["decision_record_id"],
                "reviewer_id": body.reviewer_id,
                "verdict": body.verdict,
                "notes": body.notes,
            },
        )

        await emit(
            db,
            "sample.reviewed",
            {
                "sample_id": sample_id,
                "decision_record_id": row["decision_record_id"],
                "reviewer_id": body.reviewer_id,
                "actor_role": row["assigned_role"],
                "before": {"verdict": None},
                "after": {"verdict": body.verdict},
            },
            key=str(sample_id),
            producer="decision",
        )

    return {
        "sample_id": sample_id,
        "decision_record_id": row["decision_record_id"],
        "verdict": body.verdict,
        "entry_id": entry.entry_id,
        "hash": entry.hash,
    }


@router.get("/decision-records/{record_id}", summary="One decision record")
async def get_record(record_id: str) -> dict[str, Any]:
    async with session() as db:
        row = (
            (
                await db.execute(
                    text("""
            SELECT body, superseded_by, case_id FROM app_decision.decision_record
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
    # The record is returned under its own key rather than merged with the
    # ledger's columns. `case_id` and `superseded_by` are facts the ledger
    # holds about the record, not fields of it, and a flat merge produces a
    # body that looks like a DecisionRecord and fails DecisionRecord
    # validation. A consumer that checks what it was given should not be
    # punished for it.
    return {
        "record": record,
        "case_id": row["case_id"],
        "superseded_by": row["superseded_by"],
    }


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
            SELECT required_authority, superseded_by, route, recommendation FROM app_decision.decision_record
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

        # The audit trail carries who decided and what changed, which the
        # ledger entry does not: the ledger says what was decided, the trail
        # says who did it and what the case looked like either side.
        await emit(
            db,
            "human_decision.recorded",
            {
                "human_decision_id": decision["human_decision_id"],
                "decision_record_id": body.decision_record_id,
                "case_id": body.case_id,
                "actor_id": body.actor_id,
                "actor_role": authority,
                "before": {"route": row["route"], "recommendation": row["recommendation"]},
                "after": {"final_action": body.final_action, "override": body.override},
                "policy_version": None,
            },
            key=str(body.decision_record_id),
            producer="decision",
            case_id=body.case_id,
        )

    return decision


@router.get("/human-decisions", summary="What people decided, and where they overrode")
async def human_decisions(
    case_id: str | None = None,
    override: bool | None = None,
    since: str | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, Any]:
    """The record of human decisions, for oversight rather than for a case.

    Read from `app_decision.human_decision` rather than by walking the ledger:
    the ledger is the authority on what happened, and this is an index over it
    for questions about many cases at once. Every row here has a ledger entry
    behind it.
    """
    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT h.human_decision_id, h.decision_record_id, h.case_id, h.actor_id,
                   h.authority_role, h.final_action, h.override, h.body, h.created_at,
                   r.recommendation, r.route
              FROM app_decision.human_decision h
              LEFT JOIN app_decision.decision_record r
                     ON r.decision_record_id = h.decision_record_id
             WHERE (CAST(:case_id AS text) IS NULL OR h.case_id = CAST(:case_id AS text))
               AND (CAST(:override AS boolean) IS NULL
                    OR h.override = CAST(:override AS boolean))
               AND (CAST(:since AS timestamptz) IS NULL
                    OR h.created_at >= CAST(:since AS timestamptz))
             ORDER BY h.created_at DESC
             LIMIT :limit
        """),
                    {
                        "case_id": case_id,
                        "override": override,
                        # The cast in the query tells asyncpg the type; the
                        # value has to be a datetime to match it, because the
                        # ISO string the query string carries is not one.
                        "since": datetime.fromisoformat(since) if since else None,
                        "limit": limit,
                    },
                )
            )
            .mappings()
            .all()
        )

    decisions = []
    for row in rows:
        body = row["body"]
        body = json.loads(body) if isinstance(body, str) else body
        reason = (body or {}).get("override_reason") or {}
        decisions.append(
            {
                "human_decision_id": row["human_decision_id"],
                "decision_record_id": row["decision_record_id"],
                "case_id": row["case_id"],
                "actor_id": row["actor_id"],
                "authority_role": row["authority_role"],
                "final_action": row["final_action"],
                "override": row["override"],
                "override_reason_code": reason.get("code"),
                "override_reason_text": reason.get("text"),
                # What the platform had recommended, so an override can be
                # read as a disagreement rather than as a bare action.
                "recommendation": row["recommendation"],
                "route": row["route"],
                "decided_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
        )
    return {"count": len(decisions), "decisions": decisions}


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
