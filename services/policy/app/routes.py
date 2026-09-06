"""policy-service endpoints (docs/08 §4)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter

from app.affordability import AffordabilityInputs, compute
from app.autonomy import AutonomyInputs
from app.autonomy import route as route_case
from app.db import session
from app.evaluate import evaluate_case
from app.factors import FactorScore, score_family
from app.models import (
    AffordabilityRequest,
    EvaluateRequest,
    FactorScoreRequest,
    RouteRequest,
    SandboxReplayRequest,
    SynthesizeRequest,
)
from app.packs import PackError, PolicyPack, available_packs, load_pack
from app.repository import load_replay_cases, save_sandbox_run
from app.sandbox import replay as run_replay
from app.synthesize import SynthesisInputs
from app.synthesize import synthesize as run_synthesis
from cio_common.errors import NotFound, ValidationFailed
from cio_common.ids import new_id

router = APIRouter(prefix="/policy", tags=["policy"])


def _pack(product: str, version: str | None) -> PolicyPack:
    versions = [v for p, v in available_packs() if p == product]
    if not versions:
        raise NotFound(
            f"no policy pack for product {product!r}", products=sorted({p for p, _ in available_packs()})
        )
    chosen = version or versions[-1]
    if chosen not in versions:
        raise NotFound(f"no version {chosen!r} for {product!r}", versions=versions)
    try:
        return load_pack(product, chosen)
    except PackError as exc:
        raise ValidationFailed(str(exc), problems=exc.problems) from exc


@router.get("/{product}/versions", summary="Versions on file for a product")
async def versions(product: str) -> dict[str, Any]:
    found = [v for p, v in available_packs() if p == product]
    if not found:
        raise NotFound(f"no policy pack for product {product!r}")
    return {"product": product, "versions": found, "active": found[-1]}


@router.get("/{product}/{version}", summary="One pack in full")
async def get_pack(product: str, version: str) -> dict[str, Any]:
    pack = _pack(product, version)
    return {
        "policy_version": pack.policy_version,
        "dff_version": pack.dff_version,
        "autonomy_version": pack.autonomy_version,
        "policy": pack.policy,
        "dff": pack.dff,
        "autonomy": pack.autonomy,
    }


@router.post("/evaluate", summary="Run every hard gate over a case")
async def evaluate(body: EvaluateRequest) -> dict[str, Any]:
    """Deterministic gates, affordability, exposure and authority (docs/05 §3)."""
    pack = _pack(body.product_code, body.policy_version)
    return evaluate_case(pack, body.inputs.to_policy_inputs(), snapshot_id=body.snapshot_id)


@router.post("/affordability", summary="The affordability calculation on its own")
async def affordability(body: AffordabilityRequest) -> dict[str, Any]:
    """Backs the `affordability.compute` tool (docs/06 §4)."""
    pack = _pack(body.product_code, body.policy_version)
    terms = pack.policy["product_terms"]
    section = pack.policy["affordability"]
    inputs = body.inputs

    rate = Decimal(str(terms.get("profit_rate", terms.get("markup_rate", 0))))
    income = inputs.income_verified_monthly
    commitments = inputs.commitments_monthly

    for key, value in body.overrides.items():
        if key == "income_verified_monthly":
            income = Decimal(str(value))
        elif key == "commitments_monthly":
            commitments = Decimal(str(value))
        else:
            raise ValidationFailed(
                f"unsupported affordability override {key!r}",
                supported=["income_verified_monthly", "commitments_monthly"],
            )

    result = compute(
        AffordabilityInputs(
            income_verified_monthly=income,
            commitments_monthly=commitments,
            requested_amount=inputs.requested_amount,
            tenor_months=inputs.requested_tenor,
            profit_rate=rate,
            dsr_limit=Decimal(str(section["dsr_limit"])),
            residual_income_min=Decimal(str(section["residual_income_min"])),
            stress_cases=tuple(section["stress"]),
        )
    )
    return {
        **result.as_contract(),
        "capacity_score": result.capacity_score,
        "inputs_digest": result.inputs_digest,
        "policy_version": pack.policy_version,
        "overrides_applied": sorted(body.overrides),
    }


@router.post("/factors/score", summary="Score one Decision Factor family")
async def factors_score(body: FactorScoreRequest) -> dict[str, Any]:
    """Backs the family scoring tools (docs/05 §4)."""
    pack = _pack(body.product_code, None)
    if body.family not in pack.dff["weights"]:
        raise ValidationFailed(
            f"{pack.product} does not weight the {body.family} family", families=sorted(pack.dff["weights"])
        )
    try:
        score = score_family(body.family, body.inputs)
    except TypeError as exc:
        raise ValidationFailed(f"inputs do not match the {body.family} scoring function: {exc}") from exc

    return {
        **FactorScore(
            family=score.family,
            score=score.score,
            calc_id=score.calc_id,
            tool=score.tool,
            inputs_digest=score.inputs_digest,
            evidence_refs=tuple(body.evidence_refs),
            level=score.level,
        ).as_contract(),
        "weight": pack.dff["weights"][body.family],
    }


@router.post("/synthesize", summary="Turn gates, factors and opinions into a DecisionRecord")
async def synthesize(body: SynthesizeRequest) -> dict[str, Any]:
    """The deterministic hierarchy (docs/05 §5). No model is consulted."""
    pack = _pack(body.product_code, body.policy_version)
    factors = tuple(
        FactorScore(
            family=f["family"],
            score=int(f["score"]),
            calc_id=f["calc_id"],
            tool=f.get("tool", ""),
            inputs_digest=f.get("inputs_digest", "0" * 64),
            evidence_refs=tuple(f.get("evidence_refs", [])),
            level=f.get("level"),
        )
        for f in body.factor_scores
    )
    return run_synthesis(
        SynthesisInputs(
            snapshot_id=body.snapshot_id,
            case_type=body.case_type,
            tier=body.tier,
            product_code=pack.product,
            requested_amount=float(body.requested_amount),
            policy_result=body.policy_result,
            factors=factors,
            opinions=tuple(body.opinions),
            proposed_actions=tuple(body.proposed_actions),
            dff=pack.dff,
            autonomy=pack.autonomy,
            model_versions=body.model_versions,
            committee_run_id=body.committee_run_id,
            model_health=body.model_health,
            kill_switch_active=body.kill_switch_active,
            member_watchlist=body.member_watchlist,
            active_hardship_arrangement=body.active_hardship_arrangement,
            budgets=body.budgets,
        )
    )


@router.post("/route", summary="Evaluate the Autonomy Dial for a record")
async def evaluate_route(body: RouteRequest) -> dict[str, Any]:
    """Every condition that failed is named, so an auditor can see why."""
    pack = _pack(body.product_code, body.policy_version)
    record = body.decision_record
    decision = route_case(
        pack.autonomy,
        AutonomyInputs(
            recommendation=record["recommendation"],
            confidence=record.get("confidence"),
            disagreement=record.get("disagreement"),
            challenger_open=bool(record.get("challenger_open")),
            required_authority=record["required_authority"],
            requested_amount=float(body.requested_amount),
            case_type=record["case_type"],
            snapshot_id=record["snapshot_id"],
            hard_gate_exceptions=sum(1 for g in record.get("hard_gates", []) if g["result"] == "FAIL"),
            max_open_integrity_severity=body.max_open_integrity_severity,
            member_watchlist=body.member_watchlist,
            active_hardship_arrangement=body.active_hardship_arrangement,
            model_health=body.model_health,
            kill_switch_active=body.kill_switch_active,
        ),
        pack.dff,
    )
    return {
        "route": decision.route,
        "route_reasons": decision.reasons,
        "sampled": decision.sampled,
        "failed_conditions": decision.failed_conditions,
        "autonomy_version": pack.autonomy_version,
        "setting": pack.autonomy["setting"],
    }


@router.post("/sandbox/replay", summary="Replay decided cases under a candidate pack")
async def sandbox_replay(body: SandboxReplayRequest) -> dict[str, Any]:
    """No model is called: stored opinions are reused and the code re-decides."""
    pack = _pack(body.product_code, body.policy_version)

    async with session() as db:
        cases = await load_replay_cases(
            db,
            product_code=pack.product,
            date_from=body.range.date_from,
            date_to=body.range.date_to,
            snapshot_ids=body.range.snapshot_ids or None,
            limit=body.range.limit,
        )
        if not cases:
            raise NotFound("no decided cases in that range to replay")

        report = run_replay(pack, cases, body.candidate)
        sandbox_id = new_id("sbx")
        await save_sandbox_run(
            db,
            sandbox_id=sandbox_id,
            product_code=pack.product,
            candidate=body.candidate,
            case_range=body.range.model_dump(mode="json"),
            results=report.as_dict(),
        )

    return {
        "sandbox_id": sandbox_id,
        "product_code": pack.product,
        "policy_version": pack.policy_version,
        "candidate": body.candidate,
        **report.as_dict(),
    }
