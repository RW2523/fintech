"""Tools over member-intelligence, the core stub and the RAG index (docs/06 §4).

What the cooperative knows about a member, and what the policy says. These
carry the purpose limitation most visibly: a collections call may see a
member's savings and may not see the debt-service ratio of an application
being assessed, and the registry masks what the purpose does not permit.
"""

from __future__ import annotations

from typing import Any

from ai.tools.client import services
from ai.tools.schemas import (
    ARRAY_OF_OBJECTS,
    MEMBER_ID,
    OBJECT,
    inputs,
    optional,
)
from cio_tools.registry import tool
from cio_tools.spec import EvidenceSpec, PermittedUse, SideEffect

READ = SideEffect.READ
UNDERWRITING = {PermittedUse.UNDERWRITING}
SERVICING = {PermittedUse.UNDERWRITING, PermittedUse.SERVICING, PermittedUse.COLLECTIONS}

TIMELINE_EVIDENCE = EvidenceSpec(
    type="TIMELINE_EVENT",
    source_system="member_intelligence",
    source_record_path="event_id",
    locator_from={"event_id": "event_id"},
)


@tool(
    "member.profile",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID),
    output_schema=OBJECT,
    purpose_tags=SERVICING,
    side_effects=READ,
    backing_service="member_intelligence",
    evidence=EvidenceSpec(
        type="CORE_FIELD",
        source_system="member_intelligence",
        source_record_path="member_id",
        locator_from={"field_path": "member_id"},
    ),
)
async def member_profile(member_id: str) -> dict[str, Any]:
    """Standing with the cooperative: tenure, status, branch, employer sector."""
    body = await services().get("member_intelligence", f"/members/{member_id}/profile")
    return {
        "member_id": member_id,
        "tenure_months": body.get("tenure_months"),
        "status": body.get("status"),
        "branch_id": body.get("branch_id"),
        "employer_sector": body.get("employer_sector"),
        "class": body.get("member_class"),
        "version": body.get("version"),
    }


@tool(
    "history.get",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID),
    output_schema=OBJECT,
    purpose_tags=SERVICING,
    side_effects=READ,
    backing_service="member_intelligence",
    evidence=TIMELINE_EVIDENCE,
)
async def history_get(member_id: str) -> dict[str, Any]:
    """Repayment behaviour, as the timeline records it."""
    body = await services().get("member_intelligence", f"/members/{member_id}/profile")
    return {
        "member_id": member_id,
        "ontime_rate_24m": body.get("ontime_rate_24m"),
        "arrears_12m": body.get("arrears_events_12m"),
        "months_since_last_arrears": body.get("months_since_last_arrears"),
        "restructures_36m": body.get("restructures_36m"),
        "facilities": body.get("facilities") or [],
    }


@tool(
    "timeline.get",
    version="1.0",
    input_schema=inputs(
        member_id=MEMBER_ID,
        date_from=optional({"type": "string"}),
        date_to=optional({"type": "string"}),
        types=optional({"type": "array", "items": {"type": "string"}}),
        limit=optional({"type": "integer", "minimum": 1, "maximum": 500}),
    ),
    output_schema=ARRAY_OF_OBJECTS,
    purpose_tags=SERVICING,
    side_effects=READ,
    backing_service="member_intelligence",
    evidence=TIMELINE_EVIDENCE,
)
async def timeline_get(
    member_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
    types: list[str] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """What happened, in order, with the source of each event."""
    body = await services().get(
        "member_intelligence",
        f"/members/{member_id}/timeline",
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        types=",".join(types) if types else None,
    )
    return list((body or {}).get("events") or [])


@tool(
    "savings.get",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID),
    output_schema=OBJECT,
    purpose_tags=SERVICING,
    side_effects=READ,
    backing_service="member_intelligence",
    evidence=TIMELINE_EVIDENCE,
)
async def savings_get(member_id: str) -> dict[str, Any]:
    """The savings series, its slope, and how long it has been paused."""
    body = await services().get("member_intelligence", f"/members/{member_id}/profile")
    return {
        "member_id": member_id,
        "balance": body.get("savings_balance"),
        "slope_180d": body.get("savings_slope_180d"),
        "paused_months": body.get("savings_paused_months"),
    }


@tool(
    "shares.get",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID),
    output_schema=OBJECT,
    purpose_tags=SERVICING,
    side_effects=READ,
    backing_service="member_intelligence",
    evidence=TIMELINE_EVIDENCE,
)
async def shares_get(member_id: str) -> dict[str, Any]:
    """Share capital held, and the minimum the product requires."""
    body = await services().get("member_intelligence", f"/members/{member_id}/profile")
    return {
        "member_id": member_id,
        "units": body.get("share_capital_units"),
        "ratio": body.get("share_capital_ratio"),
        "min_required": body.get("min_share_units"),
    }


@tool(
    "interactions.get",
    version="1.0",
    input_schema=inputs(
        member_id=MEMBER_ID, months=optional({"type": "integer", "minimum": 1, "maximum": 60})
    ),
    output_schema=ARRAY_OF_OBJECTS,
    purpose_tags=SERVICING,
    side_effects=READ,
    backing_service="member_intelligence",
    evidence=TIMELINE_EVIDENCE,
)
async def interactions_get(member_id: str, months: int = 24) -> list[dict[str, Any]]:
    """Contacts, promises and complaints, as recorded events."""
    events = await timeline_get(member_id, types=["INTERACTION", "COMPLAINT", "PROMISE", "ARRANGEMENT"])
    return [
        {
            "type": e.get("type"),
            "at": e.get("occurred_at"),
            "outcome": (e.get("payload") or {}).get("outcome"),
            "event_id": e.get("event_id"),
        }
        for e in events
    ][:100]


@tool(
    "hardship.get",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID),
    output_schema=OBJECT,
    purpose_tags=SERVICING,
    side_effects=READ,
    backing_service="core_stub",
)
async def hardship_get(member_id: str) -> dict[str, Any]:
    """Any arrangement in force, and what came before it.

    A member who asked for help early behaved well, and the record should be
    readable that way (docs/06 §5.6).
    """
    body = await services().get("core_stub", "/core/arrangements", member_id=member_id)
    rows = body if isinstance(body, list) else (body or {}).get("arrangements") or []
    active = [r for r in rows if str(r.get("status", "")).upper() == "ACTIVE"]
    return {
        "member_id": member_id,
        "active_arrangement": active[0] if active else None,
        "prior_requests": [r for r in rows if r not in active],
    }


@tool(
    "bureau.get",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID),
    output_schema=OBJECT,
    purpose_tags=UNDERWRITING,
    side_effects=READ,
    backing_service="core_stub",
    evidence=EvidenceSpec(
        type="CORE_FIELD",
        source_system="core_stub",
        source_record_path="member_id",
        locator_from={"field_path": "member_id"},
    ),
)
async def bureau_get(member_id: str) -> dict[str, Any]:
    """The external credit record, as the synthetic bureau holds it."""
    body = await services().get("core_stub", f"/core/bureau/{member_id}")
    return {
        "member_id": member_id,
        "grade": body.get("grade"),
        "adverse_flags": body.get("adverse_flags") or [],
        "as_of": body.get("as_of"),
    }


@tool(
    "employer.get",
    version="1.0",
    input_schema=inputs(employer_id={"type": "string", "minLength": 2}),
    output_schema=OBJECT,
    purpose_tags=SERVICING,
    side_effects=READ,
    backing_service="core_stub",
)
async def employer_get(employer_id: str) -> dict[str, Any]:
    """The employer behind a salary deduction, and its sector."""
    return await services().get("core_stub", f"/core/employers/{employer_id}")


@tool(
    "outages.get",
    version="1.0",
    input_schema=inputs(date_from=optional({"type": "string"}), date_to=optional({"type": "string"})),
    output_schema=ARRAY_OF_OBJECTS,
    purpose_tags=SERVICING,
    side_effects=READ,
    backing_service="core_stub",
)
async def outages_get(date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
    """Known processing interruptions.

    A missed deduction during an outage is an operational fact, not a member
    behaving differently (docs/07 §4.6).
    """
    body = await services().get("core_stub", "/core/outages", date_from=date_from, date_to=date_to)
    return body if isinstance(body, list) else (body or {}).get("outages") or []


@tool(
    "arrangements.get",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID),
    output_schema=ARRAY_OF_OBJECTS,
    purpose_tags=SERVICING,
    side_effects=READ,
    backing_service="core_stub",
)
async def arrangements_get(member_id: str) -> list[dict[str, Any]]:
    """Payment arrangements on this member's accounts."""
    body = await services().get("core_stub", "/core/arrangements", member_id=member_id)
    return body if isinstance(body, list) else (body or {}).get("arrangements") or []


@tool(
    "deductions.get",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID),
    output_schema=ARRAY_OF_OBJECTS,
    purpose_tags=SERVICING,
    side_effects=READ,
    backing_service="core_stub",
)
async def deductions_get(member_id: str) -> list[dict[str, Any]]:
    """What the employer remitted, cycle by cycle."""
    body = await services().get("core_stub", f"/core/members/{member_id}/deductions")
    return body if isinstance(body, list) else (body or {}).get("deductions") or []


@tool(
    "policy.lookup",
    version="1.0",
    input_schema=inputs(
        query={"type": "string", "minLength": 2},
        product_code=optional({"type": "string"}),
        policy_version=optional({"type": "string"}),
        limit=optional({"type": "integer", "minimum": 1, "maximum": 10}),
    ),
    output_schema=ARRAY_OF_OBJECTS,
    purpose_tags={
        PermittedUse.UNDERWRITING,
        PermittedUse.SERVICING,
        PermittedUse.COLLECTIONS,
        PermittedUse.FRAUD,
    },
    side_effects=READ,
    backing_service="agent_runtime",
    evidence=EvidenceSpec(
        type="RETRIEVED_CLAUSE",
        source_system="policy_corpus",
        source_record_path="clause_id",
        locator_from={"clause_id": "clause_id"},
        value_path="text",
    ),
)
async def policy_lookup(
    query: str, product_code: str | None = None, policy_version: str | None = None, limit: int = 5
) -> list[dict[str, Any]]:
    """The clauses that answer a question about policy.

    Read directly from the index rather than over HTTP: retrieval runs inside
    the agent runtime, which is where this tool is called from.
    """
    from ai.rag.index import retrieve

    hits = await retrieve(query, product=product_code, version=policy_version, limit=limit)
    return [hit.as_evidence() for hit in hits]
