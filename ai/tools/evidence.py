"""Evidence and portfolio tools (docs/06 §4).

`evidence.get` is how the Challenger checks somebody else's homework: it takes
an evidence id another agent cited and returns what that id actually points
at. `evidence.request` is how it records a gap without filling it in.
"""

from __future__ import annotations

from typing import Any

from ai.tools.client import services
from ai.tools.schemas import ARRAY_OF_OBJECTS, OBJECT, SNAPSHOT_ID, inputs, optional
from cio_tools.registry import tool
from cio_tools.spec import EvidenceSpec, PermittedUse, SideEffect

READ = SideEffect.READ
ALL_PURPOSES = {
    PermittedUse.UNDERWRITING,
    PermittedUse.SERVICING,
    PermittedUse.COLLECTIONS,
    PermittedUse.FRAUD,
}


@tool(
    "evidence.get",
    version="1.0",
    input_schema=inputs(evidence_id={"type": "string", "minLength": 3}),
    output_schema=OBJECT,
    purpose_tags=ALL_PURPOSES,
    side_effects=READ,
    backing_service="decision",
)
async def evidence_get(evidence_id: str) -> dict[str, Any]:
    """What an evidence id actually points at.

    The Challenger's main instrument: an agent's claim is only as good as the
    evidence behind it, and this is how that gets checked rather than assumed.
    """
    body = await services().get("decision", f"/evidence/{evidence_id}")
    return dict(body or {"evidence_id": evidence_id, "found": False})


@tool(
    "evidence.request",
    version="1.0",
    input_schema=inputs(
        question={"type": "string", "minLength": 4},
        requested_evidence={"type": "string"},
        requested_tool=optional({"type": "string"}),
        blocking=optional({"type": "boolean"}),
    ),
    output_schema=OBJECT,
    purpose_tags=ALL_PURPOSES,
    # Records a gap for a person to close. It changes nothing and executes
    # nothing, which is why the Challenger may hold it.
    side_effects=SideEffect.WRITE_PROPOSAL,
    backing_service="committee",
)
async def evidence_request(
    question: str, requested_evidence: str, requested_tool: str | None = None, blocking: bool = False
) -> dict[str, Any]:
    """Register a gap in the evidence, without pretending to fill it."""
    return {
        "question": question,
        "requested_evidence": requested_evidence,
        "requested_tool": requested_tool,
        "blocking": blocking,
        "recorded": True,
    }


@tool(
    "portfolio.conditions",
    version="1.0",
    input_schema=inputs(snapshot_id=SNAPSHOT_ID, employer_id=optional({"type": "string"})),
    output_schema=OBJECT,
    purpose_tags={PermittedUse.UNDERWRITING, PermittedUse.ANALYTICS},
    side_effects=READ,
    backing_service="governance",
    evidence=EvidenceSpec(
        type="ANALYTIC_RESULT",
        source_system="governance",
        source_record_path="calc_id",
        locator_from={"calc_id": "calc_id"},
    ),
)
async def portfolio_conditions(snapshot_id: str, employer_id: str | None = None) -> dict[str, Any]:
    """The CONDITIONS factor: concentration and sector stress.

    A portfolio observation. It is about the cooperative's exposure, never
    about the member in front of the officer.
    """
    return await services().post(
        "governance", "/governance/conditions", {"snapshot_id": snapshot_id, "employer_id": employer_id}
    )


@tool(
    "templates.get",
    version="1.0",
    input_schema=inputs(kind={"type": "string"}, language=optional({"type": "string"})),
    output_schema=ARRAY_OF_OBJECTS,
    purpose_tags={PermittedUse.SERVICING, PermittedUse.COLLECTIONS},
    side_effects=READ,
    backing_service="notification",
)
async def templates_get(kind: str, language: str = "en") -> list[dict[str, Any]]:
    """Approved wording for a message to a member.

    A copilot may choose a template; it may not write to a member in its own
    words (docs/06 §2.3).
    """
    body = await services().get("notification", "/templates", kind=kind, language=language)
    return list((body or {}).get("templates") or [])
