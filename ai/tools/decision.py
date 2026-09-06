"""Tools over the policy, risk and fraud services (docs/06 §4).

These are the tools that produce numbers. Every one of them returns the
number's `calc_id` or `model_run_id` alongside it, because an agent may report
a figure and may not compute one: what it reports has to be traceable to the
computation that produced it.
"""

from __future__ import annotations

from typing import Any

from ai.tools.client import services
from ai.tools.schemas import (
    ARRAY_OF_OBJECTS,
    MEMBER_ID,
    OBJECT,
    SNAPSHOT_ID,
    inputs,
    optional,
)
from cio_tools.registry import tool
from cio_tools.spec import EvidenceSpec, PermittedUse, SideEffect

READ = SideEffect.READ
UNDERWRITING = {PermittedUse.UNDERWRITING}
FRAUD = {PermittedUse.UNDERWRITING, PermittedUse.FRAUD}


@tool(
    "policy.evaluate",
    version="1.0",
    input_schema=inputs(snapshot_id=SNAPSHOT_ID),
    output_schema=OBJECT,
    purpose_tags=UNDERWRITING,
    side_effects=READ,
    backing_service="policy",
    evidence=EvidenceSpec(
        type="POLICY_RULE",
        source_system="policy",
        source_record_path="snapshot_id",
        locator_from={"rule_id": "rule_id"},
    ),
)
async def policy_evaluate(snapshot_id: str) -> dict[str, Any]:
    """Which gates pass and which block, with the rule id for each."""
    return await services().post("policy", "/policy/evaluate", {"snapshot_id": snapshot_id})


@tool(
    "affordability.compute",
    version="1.0",
    input_schema=inputs(snapshot_id=SNAPSHOT_ID, overrides=optional(OBJECT)),
    output_schema=OBJECT,
    purpose_tags=UNDERWRITING,
    side_effects=READ,
    backing_service="policy",
    evidence=EvidenceSpec(
        type="ANALYTIC_RESULT",
        source_system="policy",
        source_record_path="calc_id",
        locator_from={"calc_id": "calc_id"},
    ),
)
async def affordability_compute(snapshot_id: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """The debt-service ratio, its limit, the headroom and the stress cases."""
    body: dict[str, Any] = {"snapshot_id": snapshot_id}
    if overrides:
        body["overrides"] = overrides
    return await services().post("policy", "/policy/affordability", body)


@tool(
    "limits.get",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID, product_code={"type": "string", "minLength": 2}),
    output_schema=OBJECT,
    purpose_tags=UNDERWRITING,
    side_effects=READ,
    backing_service="policy",
    evidence=EvidenceSpec(
        type="ANALYTIC_RESULT",
        source_system="policy",
        source_record_path="calc_id",
        locator_from={"calc_id": "calc_id"},
    ),
)
async def limits_get(member_id: str, product_code: str) -> dict[str, Any]:
    """What this member may hold in total, and what is left."""
    return await services().get("policy", f"/policy/limits/{member_id}", product_code=product_code)


@tool(
    "member.commitment_score",
    version="1.0",
    input_schema=inputs(snapshot_id=SNAPSHOT_ID),
    output_schema=OBJECT,
    purpose_tags=UNDERWRITING,
    side_effects=READ,
    backing_service="policy",
    evidence=EvidenceSpec(
        type="ANALYTIC_RESULT",
        source_system="policy",
        source_record_path="calc_id",
        locator_from={"calc_id": "calc_id"},
    ),
)
async def commitment_score(snapshot_id: str) -> dict[str, Any]:
    """The COMMITMENT factor score and the inputs behind it."""
    return await services().post(
        "policy", "/policy/factors/score", {"snapshot_id": snapshot_id, "family": "COMMITMENT"}
    )


@tool(
    "actions.allowed",
    version="1.0",
    input_schema=inputs(
        state={"type": "string"}, product_code={"type": "string"}, case_type=optional({"type": "string"})
    ),
    output_schema=ARRAY_OF_OBJECTS,
    purpose_tags={PermittedUse.UNDERWRITING, PermittedUse.COLLECTIONS},
    side_effects=READ,
    backing_service="policy",
)
async def actions_allowed(
    state: str, product_code: str, case_type: str | None = None
) -> list[dict[str, Any]]:
    """What policy permits from here, and what authority each needs."""
    body = await services().get(
        "policy", "/policy/actions", state=state, product_code=product_code, case_type=case_type
    )
    return list((body or {}).get("actions") or [])


@tool(
    "risk.score",
    version="1.0",
    input_schema=inputs(snapshot_id=SNAPSHOT_ID),
    output_schema=OBJECT,
    purpose_tags=UNDERWRITING,
    side_effects=READ,
    backing_service="risk",
    evidence=EvidenceSpec(
        type="MODEL_OUTPUT",
        source_system="risk",
        source_record_path="model_run_id",
        locator_from={"model_run_id": "model_run_id"},
    ),
)
async def risk_score(snapshot_id: str) -> dict[str, Any]:
    """The probability of default, its grade, and what drove it."""
    return await services().post("risk", "/risk/score", {"snapshot_id": snapshot_id})


@tool(
    "explain.get",
    version="1.0",
    input_schema=inputs(model_run_id={"type": "string", "minLength": 3}),
    output_schema=OBJECT,
    purpose_tags=UNDERWRITING,
    side_effects=READ,
    backing_service="risk",
)
async def explain_get(model_run_id: str) -> dict[str, Any]:
    """The drivers behind one model run, with their reason codes."""
    body = await services().get("risk", f"/risk/runs/{model_run_id}")
    return {
        "model_run_id": model_run_id,
        "drivers": (body or {}).get("drivers") or [],
        "reason_codes": (body or {}).get("reason_codes") or [],
    }


@tool(
    "fraud.assess",
    version="1.0",
    input_schema=inputs(
        snapshot_id=SNAPSHOT_ID, case_id=optional({"type": "string"}), member_id=optional(MEMBER_ID)
    ),
    output_schema=OBJECT,
    purpose_tags=FRAUD,
    side_effects=READ,
    backing_service="fraud",
    evidence=EvidenceSpec(
        type="ANALYTIC_RESULT",
        source_system="fraud",
        source_record_path="assessment_id",
        locator_from={"calc_id": "calc_id"},
    ),
)
async def fraud_assess(
    snapshot_id: str, case_id: str | None = None, member_id: str | None = None
) -> dict[str, Any]:
    """Findings, the integrity score, and the level they add up to."""
    return await services().post(
        "fraud",
        "/fraud/assess",
        {"snapshot_id": snapshot_id, "case_id": case_id or snapshot_id, "member_id": member_id},
    )


@tool(
    "graph.neighbours",
    version="1.0",
    input_schema=inputs(
        case_id={"type": "string"}, depth=optional({"type": "integer", "minimum": 1, "maximum": 2})
    ),
    output_schema=OBJECT,
    purpose_tags=FRAUD,
    side_effects=READ,
    backing_service="fraud",
)
async def graph_neighbours(case_id: str, depth: int = 2) -> dict[str, Any]:
    """The neighbourhood a network finding has to be read against.

    A guarantee ring is invisible from one member's own record.
    """
    body = await services().get("fraud", f"/fraud/graph/{case_id}")
    return {
        "nodes": (body or {}).get("nodes") or [],
        "edges": (body or {}).get("edges") or [],
        "graph_ref": (body or {}).get("graph_ref"),
        "depth": depth,
    }


@tool(
    "duplicates.find",
    version="1.0",
    input_schema=inputs(case_id={"type": "string"}),
    output_schema=ARRAY_OF_OBJECTS,
    purpose_tags=FRAUD,
    side_effects=READ,
    backing_service="fraud",
)
async def duplicates_find(case_id: str) -> list[dict[str, Any]]:
    """Other cases this one shares an identity or a document with."""
    body = await services().get("fraud", f"/fraud/signals/{case_id}")
    return [
        {
            "code": f.get("code"),
            "severity": f.get("severity"),
            "rule": f.get("rule"),
            "members": f.get("members") or [],
            "detail": f.get("detail"),
        }
        for f in (body or {}).get("findings") or []
        if f.get("rule") in {"duplicate_applicant", "reused_image"}
    ]
