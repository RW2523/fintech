"""agent-runtime endpoints (docs/06 §1, docs/08 §5)."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ai.agents.bundle import list_bundles, load_bundle
from app.gateway_client import GatewayClient, HttpGatewayClient
from app.guided import prune_defs, simplify
from app.invoke import OPINION_SCHEMA_ID, Invocation, invoke
from cio_common.errors import NotFound, ValidationFailed
from cio_contracts import bundle as contract_bundle

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
