"""Activities for the underwriting workflow (docs/08 §8.1).

Each activity is a thin call to the service that owns the step. Document,
feature, risk, fraud and committee steps return deterministic placeholders
until P3 and P4 replace them; the placeholders are marked so a partial run is
never mistaken for a real assessment.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx
from temporalio import activity

from workflows.shared import (
    CaseRef,
    CommitteeOutcome,
    DecisionOutcome,
    ModelOutcome,
    PolicyOutcome,
)

__all__ = [
    "ACTIVITIES",
    "decide",
    "evaluate_policy",
    "freeze_snapshot",
    "issue_token",
    "record_decision",
    "record_human_decision",
    "run_committee",
    "score_models",
]

_TIMEOUT = 30.0


def _url(service: str, default_port: int) -> str:
    override = os.environ.get(f"{service.upper()}_URL")
    return override or f"http://{service}:{default_port}"


async def _post(service: str, port: int, path: str, body: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(f"{_url(service, port)}{path}", json=body)
        response.raise_for_status()
        return dict(response.json())


async def _get(service: str, port: int, path: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(f"{_url(service, port)}{path}")
        response.raise_for_status()
        return dict(response.json())


@activity.defn
async def freeze_snapshot(case: CaseRef) -> CaseRef:
    """Read the frozen snapshot the submit already wrote. Idempotent."""
    snapshot = await _get("application", 8001, f"/cases/{case.case_id}/snapshot")
    return CaseRef(
        case_id=case.case_id,
        snapshot_id=snapshot["snapshot_id"],
        member_id=snapshot["member_id"],
        product_code=snapshot["product_code"],
        requested_amount=snapshot.get("requested_amount", "0.00"),
        application_id=case.application_id,
    )


@activity.defn
async def evaluate_policy(case: CaseRef, inputs: dict[str, Any]) -> PolicyOutcome:
    """Deterministic gates. This never depends on a model being available."""
    result = await _post(
        "policy",
        8004,
        "/policy/evaluate",
        {
            "product_code": case.product_code,
            "snapshot_id": case.snapshot_id,
            "inputs": inputs,
        },
    )
    return PolicyOutcome(
        policy_result=result,
        blockers=list(result.get("blockers", [])),
        required_authority=result["required_authority"],
        evidence_coverage=result["evidence_coverage"],
    )


@activity.defn
async def score_models(case: CaseRef) -> ModelOutcome:
    """Placeholder for risk and fraud scoring (T-032, T-033).

    Returns `available=False` so the workflow forces a human route, per the
    fail-safe matrix in docs/13 §7.
    """
    return ModelOutcome(
        available=False, risk={"placeholder": True}, fraud={"placeholder": True, "level": "NONE"}
    )


@activity.defn
async def run_committee(case: CaseRef, tier: str) -> CommitteeOutcome:
    """Placeholder for the AI Credit Council (T-044).

    Produces no opinions, so the Synthesizer decides on gates and factors alone
    and the record is marked degraded.
    """
    return CommitteeOutcome(tier=tier, run_id=None, factor_scores=[], opinions=[], degraded=True)


@activity.defn
async def decide(
    case: CaseRef, policy: PolicyOutcome, committee: CommitteeOutcome, model_health: str
) -> dict[str, Any]:
    """Deterministic synthesis. No model is consulted here (CLAUDE.md §2.1)."""
    return await _post(
        "policy",
        8004,
        "/policy/synthesize",
        {
            "product_code": case.product_code,
            "snapshot_id": case.snapshot_id,
            "case_type": "ORIGINATION",
            "tier": committee.tier,
            "requested_amount": case.requested_amount,
            "policy_result": policy.policy_result,
            "factor_scores": committee.factor_scores,
            "opinions": committee.opinions,
            "committee_run_id": committee.run_id,
            "model_health": model_health,
            "model_versions": {},
        },
    )


@activity.defn
async def record_decision(case: CaseRef, record: dict[str, Any]) -> DecisionOutcome:
    """Append the record to the ledger. Nothing proceeds without this."""
    appended = await _post(
        "decision",
        8012,
        "/recommendations",
        {
            "decision_record": record,
            "case_id": case.case_id,
            "member_id": case.member_id,
        },
    )
    return DecisionOutcome(
        decision_record_id=appended["decision_record_id"],
        recommendation=record["recommendation"],
        route=record["route"],
        required_authority=record["required_authority"],
        ledger_entry_id=appended["entry_id"],
    )


@activity.defn
async def record_human_decision(
    case: CaseRef, decision_record_id: str, signal: dict[str, Any]
) -> dict[str, Any]:
    """Authority is enforced by decision-service, not here."""
    return await _post(
        "decision",
        8012,
        "/human-decisions",
        {
            "decision_record_id": decision_record_id,
            "case_id": case.case_id,
            **signal,
        },
    )


@activity.defn
async def issue_token(
    case: CaseRef, decision_record_id: str, human_decision_id: str | None
) -> dict[str, Any]:
    return await _post(
        "decision",
        8012,
        "/tokens",
        {
            "action_id": f"act_{case.snapshot_id[5:]}",
            "decision_record_id": decision_record_id,
            "case_id": case.case_id,
            "member_id": case.member_id,
            "product_code": case.product_code,
            "max_amount": case.requested_amount,
            "idempotency_key": f"underwrite-{case.snapshot_id}",
            "human_decision_id": human_decision_id,
        },
    )


#: Registered with the worker in workflows/worker.py.
ACTIVITIES: list[Callable[..., Any]] = [
    freeze_snapshot,
    evaluate_policy,
    score_models,
    run_committee,
    decide,
    record_decision,
    record_human_decision,
    issue_token,
]
