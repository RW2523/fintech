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
        tenor_months=int(snapshot.get("tenor_months") or 0),
        instalment=str(snapshot.get("instalment") or "0.00"),
        profit_rate=str(snapshot.get("profit_rate") or "0"),
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


@activity.defn
async def execute_action(
    case: CaseRef, decision_record_id: str, token_id: str, human_decision_id: str | None
) -> dict[str, Any]:
    """Turn an approval into a facility (docs/08 §7).

    The proposal is recorded first, then carried out. Two calls rather than
    one because the proposal is what the token was issued against: an execute
    that also created its own proposal could execute something nobody
    approved.

    A core that refuses leaves the action retryable and the outcome pending.
    The activity reports that rather than raising, so the workflow can decide
    whether to retry or wait for a person instead of being retried blindly by
    Temporal into the same failing write.
    """
    action_id = f"act_{case.snapshot_id[5:]}"
    proposal = {
        "action_id": action_id,
        "case_id": case.case_id,
        "member_id": case.member_id,
        "decision_record_id": decision_record_id,
        "human_decision_id": human_decision_id,
        "level": "L3",
        "type": "APPROVE_FINANCING",
        "parameters": {
            "product_code": case.product_code,
            "amount": case.requested_amount,
            "tenor_months": case.tenor_months,
            "instalment": case.instalment,
            "profit_rate": case.profit_rate,
        },
        "rationale": {"text": "approved on the decision record", "evidence_refs": []},
        "requires": "OFFICER",
        "proposed_by": "policy",
    }
    await _post("execution", 8013, "/action-proposals", proposal)

    try:
        return await _post(
            "execution",
            8013,
            f"/actions/{action_id}/execute",
            {"token_id": token_id, "product_code": case.product_code},
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            detail = dict((exc.response.json().get("error") or {}).get("details") or {})
            return {"action_id": action_id, "state": "FAILED", "retryable": True, **detail}
        raise


# ---------------------------------------------------------------------------
# the early-warning case (docs/08 §8.2)
# ---------------------------------------------------------------------------
@activity.defn
async def read_member_watch(case: CaseRef) -> dict[str, Any]:
    """Where the state machine has put this member, and what it saw.

    Read rather than recomputed: the nightly pass already decided, and a case
    that recomputed the state would be able to disagree with the alert that
    opened it.
    """
    try:
        state = await _get("lmi", 8008, f"/lmi/state/{case.member_id}")
    except httpx.HTTPError:
        # A member the engine cannot speak about is not a member in trouble.
        return {"state": "STABLE", "available": False}

    features: dict[str, Any] = {}
    scores: dict[str, Any] = {}
    try:
        features = await _get("lmi", 8008, f"/lmi/features/{case.member_id}")
        scores = await _post("lmi", 8008, "/lmi/score", {"member_id": case.member_id})
    except httpx.HTTPError:
        # The state stands without them; the Council is told what is missing
        # rather than handed zeros.
        pass

    return {
        "state": state.get("state"),
        "since": state.get("since"),
        "rule": state.get("rule"),
        "reason": state.get("reason"),
        "transitions": state.get("transitions") or [],
        "features": features.get("features") or {},
        "stale_days": features.get("stale_days"),
        "scores": scores.get("scores") or [],
        "model_version": scores.get("model_version"),
        "available": True,
    }


@activity.defn
async def run_longitudinal_council(case: CaseRef, alert_id: str, watch: dict[str, Any]) -> dict[str, Any]:
    """The Longitudinal Council, through the committee orchestrator.

    The same orchestrator as an application: the case type is what differs, and
    keeping one state machine means an early-warning deliberation is auditable
    the same way an origination one is.
    """
    scores = {int(s["horizon_days"]): s for s in watch.get("scores") or []}
    p30 = scores.get(30, {})

    request = {
        "snapshot": {
            "snapshot_id": case.snapshot_id or f"snap_{case.case_id[5:]}",
            "case_id": case.case_id,
            "product_code": case.product_code,
            "amount": case.requested_amount,
            "member": {"member_ref": case.member_id},
        },
        "policy_result": {
            "blockers": [],
            "flags": [],
            "rules": [],
            "evidence_coverage": 1.0 if watch.get("features") else 0.0,
            "required_authority": "CREDIT_OFFICER",
            "policy_version": f"policy/{case.product_code}/2026.09.1",
        },
        "case_type": "EARLY_WARNING",
        "tier": "STANDARD",
        # What the Council is reasoning about, so an opinion can cite it.
        "tool_results": {
            "behaviour_trend": [
                {"tool": "state.get", "result": watch, "evidence_refs": []},
            ],
            "forecast_scenario": [
                {"tool": "lmi.score", "result": {"scores": watch.get("scores")}, "evidence_refs": []},
            ],
        },
        "model_health": "GREEN" if watch.get("available") else "AMBER",
    }

    try:
        body = await _post("committee", 8009, "/committee/runs", request, timeout=_COMMITTEE_TIMEOUT)
    except httpx.HTTPError:
        # No deliberation. The case still gets a record, from the state machine
        # and the model, because an alert nobody can act on is worse than one
        # decided without agents.
        return {
            "decision_record_id": "",
            "recommendation": "MONITOR" if watch.get("state") != "CRITICAL" else "INTERVENE",
            "route": "OFFICER_REVIEW",
            "proposed_actions": [],
            "degraded": True,
            "detail": "the committee was unavailable; decided from the state and the model",
            "p30": p30.get("probability"),
        }

    record = dict(body.get("decision_record") or {})
    return {
        "decision_record_id": record.get("decision_record_id", ""),
        "recommendation": record.get("recommendation", "MONITOR"),
        "route": record.get("route", "OFFICER_REVIEW"),
        "proposed_actions": record.get("proposed_actions") or [],
        "degraded": bool(body.get("degraded_agents")),
        "p30": p30.get("probability"),
    }


@activity.defn
async def carry_out_intervention(
    case: CaseRef, decision_record_id: str, action_ids: list[str], signal: dict[str, Any]
) -> dict[str, Any]:
    """Carry out the actions a person approved, and no others.

    Each is proposed to the execution service and executed under the approver's
    name. An action nobody approved is not attempted, and one that fails leaves
    the rest alone: these are separate contacts with a member, not a
    transaction.
    """
    executed: list[str] = []
    failed: list[dict[str, Any]] = []

    for action_id in action_ids:
        try:
            await _post(
                "execution",
                8013,
                f"/actions/{action_id}/execute",
                {"token_id": f"tok_{action_id[4:]}", "product_code": case.product_code},
            )
            executed.append(action_id)
        except httpx.HTTPError as exc:
            failed.append({"action_id": action_id, "detail": str(exc)})

    return {
        "executed": executed,
        "failed": failed,
        "approved_by": signal.get("actor_id"),
        "decision_record_id": decision_record_id,
    }


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
    execute_action,
    read_member_watch,
    run_longitudinal_council,
    carry_out_intervention,
    record_human_decision,
    issue_token,
]
