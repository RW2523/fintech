"""committee-orchestrator endpoints (docs/06 §8, docs/08 §6)."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import yaml
from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field

from app import repository
from app.clients import Clients, HttpClients
from app.db import session
from app.orchestrate import COUNCIL, run_committee
from app.tiers import select_tier
from cio_common.assets import policy_pack_root
from cio_common.errors import NotFound, ValidationFailed

router = APIRouter(tags=["committee"])

PACK_ROOT = policy_pack_root()

#: Swapped for a fake in tests.
_clients: Clients | None = None


def clients() -> Clients:
    global _clients
    if _clients is None:
        _clients = HttpClients(
            agent_runtime_url=os.environ.get("AGENT_RUNTIME_URL", "http://agent_runtime:8011"),
            policy_url=os.environ.get("POLICY_URL", "http://policy:8004"),
            decision_url=os.environ.get("DECISION_URL", "http://decision:8012"),
            llm_url=os.environ.get("LLM_GATEWAY_URL", "http://llm_gateway:8020"),
        )
    return _clients


def set_clients(replacement: Clients | None) -> None:
    global _clients
    _clients = replacement


@lru_cache(maxsize=8)
def tier_rules(product_code: str, version: str | None = None) -> dict[str, Any]:
    """The tier_selection block of a product's policy pack."""
    directory = PACK_ROOT / product_code
    if not directory.is_dir():
        raise KeyError(product_code)
    chosen = (directory / version) if version else sorted(p for p in directory.iterdir() if p.is_dir())[-1]
    pack = yaml.safe_load((chosen / "policy.yaml").read_text())
    return dict(pack.get("tier_selection") or {})


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: dict[str, Any]
    policy_result: dict[str, Any] = Field(default_factory=dict)
    #: Per-agent tool results, keyed by agent id. Gathered by the caller,
    #: because the orchestrator does not decide what an agent may read.
    tool_results: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    case_type: str = "ORIGINATION"
    tier: str | None = None
    fraud_level: str | None = None
    identity_mismatch: bool = False
    active_hardship: bool = False
    clean_12m: bool = True
    top_band: bool = False
    model_health: str = Field(default="GREEN", pattern="^(GREEN|AMBER|RED)$")


@router.post("/committee/runs", summary="Deliberate on a frozen snapshot", status_code=202)
async def create_run(body: RunRequest, response: Response) -> dict[str, Any]:
    """docs/06 §8 — idempotent on snapshot and tier.

    A second submission of the same case at the same tier joins the run that
    exists. The inputs are frozen, so a second deliberation over them would
    produce a second answer to a question already settled.
    """
    snapshot = body.snapshot
    snapshot_id = str(snapshot.get("snapshot_id") or "")
    if not snapshot_id:
        raise ValidationFailed("the snapshot has no snapshot_id")

    product = str(snapshot.get("product_code") or "PF-STD")
    try:
        rules = tier_rules(product)
    except (KeyError, IndexError) as exc:
        raise ValidationFailed(f"no policy pack for {product!r}") from exc

    decision = select_tier(
        tier_rules=rules,
        requested_amount=float(snapshot.get("amount") or 0),
        policy_result=body.policy_result,
        fraud_level=body.fraud_level,
        identity_mismatch=body.identity_mismatch,
        active_hardship=body.active_hardship,
        clean_12m=body.clean_12m,
        top_band=body.top_band,
        model_health=body.model_health,
    )
    if body.tier:
        decision = type(decision)(tier=body.tier, reasons=("REQUESTED_BY_CALLER",), budget=decision.budget)

    async with session() as db:
        existing = await repository.run_for_snapshot(db, snapshot_id, decision.tier)
        if existing:
            response.status_code = 200
            existing["opinions"] = await repository.list_opinions(db, existing["run_id"])
            existing["idempotent"] = True
            return existing

    result = await run_committee(
        clients=clients(),
        snapshot=snapshot,
        tier=decision,
        tool_results=body.tool_results,
        policy_result=body.policy_result,
        case_type=body.case_type,
        model_health=body.model_health,
    )

    async with session() as db:
        await repository.save_run(
            db,
            result,
            case_id=snapshot.get("case_id"),
            case_type=body.case_type,
            tier_reasons=list(decision.reasons),
        )

    payload = result.as_contract()
    payload["tier_reasons"] = list(decision.reasons)
    payload["decision_record"] = result.decision_record
    return payload


@router.get("/committee/runs/{run_id}", summary="One run and its opinions")
async def get_run(run_id: str) -> dict[str, Any]:
    async with session() as db:
        found = await repository.find_run(db, run_id)
    if found is None:
        raise NotFound(f"no committee run {run_id}")
    return found


@router.get("/committee/runs/{run_id}/opinions", summary="The opinions in a run")
async def get_opinions(run_id: str) -> dict[str, Any]:
    async with session() as db:
        found = await repository.find_run(db, run_id)
        if found is None:
            raise NotFound(f"no committee run {run_id}")
        opinions = await repository.list_opinions(db, run_id)
    return {"run_id": run_id, "count": len(opinions), "opinions": opinions}


def version_detail() -> dict[str, Any]:
    return {"council": list(COUNCIL), "tiers": ["FAST", "STANDARD", "EXTENDED"]}
