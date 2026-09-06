"""agent-runtime endpoints (docs/06 §1, docs/08 §5)."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ai.agents.bundle import list_bundles, load_bundle
from ai.tools import registry, tool_names
from app.gateway_client import GatewayClient, HttpGatewayClient
from app.guided import prune_defs, simplify
from app.invoke import OPINION_SCHEMA_ID, Invocation, invoke
from cio_common.errors import Forbidden, NotFound, ValidationFailed
from cio_contracts import bundle as contract_bundle
from cio_tools.grants import Grant, GrantRegistry, ToolDenied
from cio_tools.registry import ToolContext
from cio_tools.spec import PermittedUse, SideEffect

router = APIRouter(tags=["agent_runtime"])

#: Swapped for a fake in tests.
_client: GatewayClient | None = None


def gateway_client() -> GatewayClient:
    global _client
    if _client is None:
        _client = HttpGatewayClient(os.environ.get("LLM_GATEWAY_URL"))
    return _client


def set_gateway_client(client: GatewayClient | None) -> None:
    global _client
    _client = client


@lru_cache(maxsize=1)
def opinion_schema() -> dict[str, Any]:
    """The AgentOpinion contract, as a schema the model is given.

    Taken from the published contract rather than restated here, so an agent
    is asked for exactly the shape the rest of the platform validates.
    """
    document = contract_bundle()
    return {"$schema": document["$schema"], "$ref": "#/$defs/AgentOpinion", "$defs": document["$defs"]}


#: The fields the runtime sets itself (docs/06 §1). An agent that could set its
#: own id or version could claim to be a different agent, so the model is asked
#: only for what it decides.
MODEL_FIELDS = (
    "stance",
    "confidence",
    "reason_codes",
    "claims",
    "contradictions",
    "unresolved",
    "proposed_actions",
    "factor_scores",
    "changed_from_prior",
)


@lru_cache(maxsize=1)
def model_output_schema() -> dict[str, Any]:
    """The part of an opinion the model is responsible for."""
    full = opinion_schema()
    opinion = full["$defs"]["AgentOpinion"]
    # Simplified for the grammar engine: a decoder cannot compile
    # `propertyNames`, and the runtime revalidates the real contract anyway.
    return prune_defs(
        simplify(
            {
                "$schema": full["$schema"],
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "stance",
                    "confidence",
                    "reason_codes",
                    "claims",
                    "contradictions",
                    "unresolved",
                    "proposed_actions",
                ],
                "properties": {
                    name: opinion["properties"][name]
                    for name in MODEL_FIELDS
                    if name in opinion["properties"]
                },
                "$defs": full["$defs"],
            }
        )
    )


class InvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=2, max_length=64)
    committee_run_id: str = Field(min_length=3, max_length=64)
    snapshot: dict[str, Any]
    round: str = Field(default="ASSESS", pattern="^(ASSESS|CHALLENGE|REVISE|TEMPORAL)$")
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    clauses: list[dict[str, Any]] = Field(default_factory=list)
    prior_opinions: list[dict[str, Any]] = Field(default_factory=list)
    temporal_context: dict[str, Any] | None = None
    run_id: str = ""
    budget_tokens: int = Field(default=0, ge=0)


class ToolCallRequest(BaseModel):
    """A tool call made by the orchestrator, not by an agent (docs/06 §8).

    The Challenger names a gap and the tool that would fill it; the
    orchestrator calls that tool itself. An agent allowed to fill its own gap
    would be deciding what counts as evidence about its own case.
    """

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=3, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)
    committee_run_id: str = Field(min_length=3, max_length=64)
    #: Whose behalf. Only the workflow may repair evidence; an agent id here
    #: would be an agent calling a tool outside its own invocation.
    principal: str = Field(default="workflow", pattern="^workflow$")
    purpose: str = Field(default="UNDERWRITING")
    case_id: str | None = None
    member_id: str | None = None
    max_calls: int = Field(default=2, ge=1, le=10)


@router.get("/tools", summary="Every registered tool")
async def tools() -> dict[str, Any]:
    return {"tools": list(tool_names()), "count": len(tool_names())}


@router.post("/tools/call", summary="Call one tool as the workflow")
async def call_tool(body: ToolCallRequest) -> dict[str, Any]:
    """Run a tool outside any agent's invocation, for evidence repair.

    The grant is minted for this call rather than read from an agent bundle:
    the caller is the orchestrator, which has no bundle, and the tool's own
    purpose tags and field purposes still decide what comes back.
    """
    try:
        purpose = PermittedUse[body.purpose.upper()]
    except KeyError as exc:
        raise ValidationFailed(
            f"unknown purpose {body.purpose!r}",
            purposes=[p.name for p in PermittedUse],
        ) from exc

    try:
        spec = registry.get(body.tool)
    except KeyError as exc:
        raise NotFound(str(exc)) from exc

    if spec.side_effects is SideEffect.WRITE_PROPOSAL and spec.name != "evidence.request":
        # Repair may read, and may register a request for something missing.
        # It may not act on the case: acting is a person's to do, and a
        # Challenger that could name an action tool could move the case by
        # asking for it.
        raise Forbidden(
            f"tool {body.tool!r} writes, and evidence repair may only read",
            tool=body.tool,
        )

    scoped = registry.with_grants(
        GrantRegistry([Grant(agent_id=body.principal, tool=body.tool, max_calls=body.max_calls)])
    )

    context = ToolContext(
        agent_id=body.principal,
        run_id=body.committee_run_id,
        purpose=purpose,
        principal=body.principal,
        case_id=body.case_id,
        member_id=body.member_id,
    )

    try:
        result = await scoped.call(body.tool, dict(body.args), context)
    except ToolDenied as denied:
        raise Forbidden(str(denied), tool=body.tool, reason=denied.reason) from denied

    invocation = scoped.invocations[-1] if scoped.invocations else None
    return {
        "tool": body.tool,
        "version": spec.version,
        "result": result,
        "call_id": invocation.call_id if invocation else None,
        "evidence_ids": list(invocation.evidence_ids) if invocation else [],
        "seconds": round(invocation.seconds, 4) if invocation else 0.0,
    }


@router.get("/agents", summary="Every agent bundle and its version")
async def agents() -> dict[str, Any]:
    entries = []
    for agent_id in list_bundles():
        try:
            entries.append(load_bundle(agent_id).as_dict())
        except (ValueError, FileNotFoundError) as exc:
            entries.append({"agent_id": agent_id, "error": str(exc)})
    return {"agents": entries, "count": len(entries), "output_schema": OPINION_SCHEMA_ID}


@router.post("/agents/invoke", summary="Run one agent and return its opinion")
async def invoke_agent(body: InvokeRequest) -> dict[str, Any]:
    try:
        agent = load_bundle(body.agent_id)
    except FileNotFoundError as exc:
        raise NotFound(f"no agent bundle {body.agent_id!r}") from exc
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from exc

    if body.round == "REVISE" and not body.prior_opinions:
        # docs/06 §3 — a revision without the prior round is not a revision.
        raise ValidationFailed("a REVISE round needs prior_opinions", agent_id=body.agent_id)

    result = await invoke(
        Invocation(
            bundle=agent,
            snapshot=body.snapshot,
            committee_run_id=body.committee_run_id,
            round=body.round,
            tool_results=body.tool_results,
            clauses=body.clauses,
            prior_opinions=body.prior_opinions,
            temporal_context=body.temporal_context,
            run_id=body.run_id or body.committee_run_id,
            budget_tokens=body.budget_tokens,
        ),
        client=gateway_client(),
        output_schema=model_output_schema(),
    )
    return result.as_contract()
