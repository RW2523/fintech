"""Activities for the underwriting workflow (docs/08 §8.1).

Each activity is a thin call to the service that owns the step. Document,
A step whose service cannot answer returns an outcome that says so rather than
an empty one: a model that did not run is not a model that found nothing, and
the workflow routes such a case to a person (docs/13 §7).
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
    DocumentOutcome,
    FeatureOutcome,
    ModelOutcome,
    PolicyOutcome,
)

__all__ = [
    "ACTIVITIES",
    "compute_features",
    "decide",
    "evaluate_policy",
    "freeze_snapshot",
    "gather_documents",
    "issue_token",
    "record_decision",
    "record_human_decision",
    "run_committee",
    "score_models",
]

#: Reads and deterministic calls finish in well under this. The committee is
#: the exception and gets its own, because a Council round is minutes of model
#: decoding rather than a query.
_TIMEOUT = 30.0
_COMMITTEE_TIMEOUT = 900.0


def _url(service: str, default_port: int) -> str:
    override = os.environ.get(f"{service.upper()}_URL")
    return override or f"http://{service}:{default_port}"


async def _post(
    service: str, port: int, path: str, body: dict[str, Any], *, timeout: float = _TIMEOUT
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
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
async def gather_documents(case: CaseRef) -> DocumentOutcome:
    """What the document service holds for this case.

    A case file that cannot be read is not an empty case file, so a failure
    here is reported rather than returned as "no findings".
    """
    try:
        documents = await _get("document", 8002, f"/cases/{case.case_id}/documents")
        findings = await _get("document", 8002, f"/cases/{case.case_id}/findings")
    except httpx.HTTPError:
        return DocumentOutcome(available=False)
    return DocumentOutcome(
        documents=list(documents.get("documents") or []),
        findings=list(findings.get("findings") or []),
        available=True,
    )


@activity.defn
async def compute_features(case: CaseRef) -> FeatureOutcome:
    """Freeze the feature snapshot the models will score.

    Done once, before either model runs, so risk and fraud reason about the
    same inputs and the decision cites one snapshot rather than two.
    """
    try:
        snapshot = await _post("feature", 8005, "/features/snapshot", {"member_id": case.member_id})
    except httpx.HTTPError:
        return FeatureOutcome(available=False)
    return FeatureOutcome(
        snapshot_id=snapshot["snapshot_id"], features=dict(snapshot.get("features") or {}), available=True
    )


@activity.defn
async def score_models(case: CaseRef, feature_snapshot_id: str = "") -> ModelOutcome:
    """Risk and fraud, on the frozen feature snapshot.

    Either being unavailable forces a human route: a model that did not run is
    not a model that found nothing (docs/13 §7).
    """
    snapshot_id = feature_snapshot_id or case.snapshot_id
    unavailable: list[str] = []
    risk: dict[str, Any] = {}
    fraud: dict[str, Any] = {}

    try:
        risk = await _post("risk", 8006, "/risk/score", {"snapshot_id": snapshot_id})
    except httpx.HTTPError as exc:
        unavailable.append(f"risk: {exc}")
    try:
        fraud = await _post(
            "fraud",
            8007,
            "/fraud/assess",
            {"snapshot_id": snapshot_id, "case_id": case.case_id, "member_id": case.member_id},
        )
    except httpx.HTTPError as exc:
        unavailable.append(f"fraud: {exc}")

    return ModelOutcome(available=not unavailable, risk=risk, fraud=fraud, unavailable=unavailable)


@activity.defn
async def run_committee(case: CaseRef, request: dict[str, Any]) -> CommitteeOutcome:
    """The AI Credit Council, through the orchestrator.

    A committee that cannot be reached leaves a degraded outcome rather than
    stopping the workflow: the Synthesizer decides on gates and factors alone,
    and the record says the deliberation did not happen.
    """
    try:
        body = await _post("committee", 8009, "/committee/runs", request, timeout=_COMMITTEE_TIMEOUT)
    except httpx.HTTPError:
        return CommitteeOutcome(tier=str(request.get("tier") or "STANDARD"), run_id=None, degraded=True)

    record = dict(body.get("decision_record") or {})
    return CommitteeOutcome(
        tier=str(body.get("tier") or "STANDARD"),
        run_id=body.get("run_id"),
        factor_scores=[
            {"family": family, **score} for family, score in (record.get("factor_scores") or {}).items()
        ],
        opinions=list(body.get("opinions") or []),
        degraded=bool(body.get("degraded_agents")) or bool(body.get("timed_out")),
        decision_record=record,
        tier_reasons=list(body.get("tier_reasons") or []),
        timed_out=bool(body.get("timed_out")),
    )


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
    # The sampling rate, the reviewing role and the deadline belong to the
    # policy pack, so they are read from the dial rather than defaulted in the
    # decision service. A rate the institution lowered must take effect on the
    # next decision, not on the next deployment.
    sampling: dict[str, Any] = {}
    if record.get("sampled"):
        try:
            dial = await _get("policy", 8004, f"/autonomy/{case.product_code}")
            sampling = dict(dial.get("sampling") or {})
        except httpx.HTTPError:
            # The review still gets queued, on the decision service's own
            # defaults. A sample nobody reviews is worse than one reviewed by
            # the wrong role.
            sampling = {}

    appended = await _post(
        "decision",
        8012,
        "/recommendations",
        {
            "decision_record": record,
            "case_id": case.case_id,
            "member_id": case.member_id,
            "sampling": sampling,
        },
    )
    return DecisionOutcome(
        decision_record_id=appended["decision_record_id"],
        recommendation=record["recommendation"],
        route=record["route"],
        required_authority=record["required_authority"],
        ledger_entry_id=appended["entry_id"],
        sample_id=appended.get("sample_id"),
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
    gather_documents,
    compute_features,
    score_models,
    run_committee,
    decide,
    record_decision,
    record_human_decision,
    issue_token,
]
