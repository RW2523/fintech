"""policy-service endpoints (docs/08 §4)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter

from app.affordability import AffordabilityInputs, compute
from app.autonomy import AutonomyInputs
from app.autonomy import route as route_case
from app.db import session
from app.dial import (
    KILL_SWITCH_OWNERS,
    AmendmentError,
    amend,
    check_approvers,
    effective_setting,
    version_for,
)
from app.evaluate import evaluate_case
from app.factors import FactorScore, score_family
from app.models import (
    AffordabilityRequest,
    AutonomyChangeRequest,
    EvaluateRequest,
    FactorScoreRequest,
    KillSwitchRequest,
    RouteRequest,
    SandboxReplayRequest,
    SynthesizeRequest,
)
from app.packs import PackError, PolicyPack, available_packs, load_pack
from app.repository import (
    active_amendment,
    amendment_history,
    kill_switch_active,
    kill_switch_state,
    load_replay_cases,
    next_amendment_sequence,
    save_amendment,
    save_sandbox_run,
    set_kill_switch,
)
from app.sandbox import replay as run_replay
from app.synthesize import SynthesisInputs
from app.synthesize import synthesize as run_synthesis
from cio_common.errors import Forbidden, NotFound, ValidationFailed
from cio_common.ids import new_id
from cio_common.outbox import emit

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


@router.get("/products", summary="Products with a policy pack on file")
async def products() -> dict[str, Any]:
    """Listed before the wildcard route below, which would otherwise treat
    "products" as a product code and answer 404."""
    return {"products": sorted({p for p, _ in available_packs()})}


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

    # The dial and the switch are read here, not taken from the caller. A
    # caller that forgot to send `kill_switch_active` would otherwise get a
    # decision made as though the switch were off, which is the one mistake a
    # kill switch must not permit. An explicit true from the caller still
    # holds, so a sandbox can ask what a case would do under a stop.
    autonomy, autonomy_version, switched = await effective_autonomy(body.product_code, body.policy_version)

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
            autonomy=autonomy,
            autonomy_version=autonomy_version,
            model_versions=body.model_versions,
            committee_run_id=body.committee_run_id,
            model_health=body.model_health,
            kill_switch_active=body.kill_switch_active or switched,
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


# ---------------------------------------------------------------------------
# the Autonomy Dial (docs/05 §6, docs/08 §6)
#
# These sit on their own router, without the /policy prefix, because the paths
# the API contract publishes are /autonomy/{product} and /kill-switch/{product}.
# ---------------------------------------------------------------------------
dial_router = APIRouter(tags=["autonomy"])


async def effective_autonomy(product: str, version: str | None = None) -> tuple[dict[str, Any], str, bool]:
    """The dial as it stands: the pack's document, any amendment over it, and
    whether the kill switch is currently holding it down."""
    pack = _pack(product, version)
    async with session() as db:
        amendment = await active_amendment(db, product)
        switched = await kill_switch_active(db, product)
    if amendment is None:
        return dict(pack.autonomy), pack.autonomy_version, switched
    return dict(amendment["body"]), str(amendment["version"]), switched


@dial_router.get("/autonomy/{product}", summary="Where the dial is set")
async def get_autonomy(product: str) -> dict[str, Any]:
    autonomy, version, switched = await effective_autonomy(product)
    async with session() as db:
        switch = await kill_switch_state(db, product)
        history = await amendment_history(db, product)

    return {
        "product_code": product,
        "autonomy_version": version,
        "setting": autonomy.get("setting"),
        # What the dial would read if the switch were released, so an operator
        # can see what they are going back to before they release it.
        "effective_setting": effective_setting(autonomy, kill_switch=switched),
        "kill_switch": switch,
        "bands": autonomy.get("bands", []),
        "autonomous_conditions": autonomy.get("autonomous_conditions", {}),
        "sampling": autonomy.get("sampling", {}),
        "history": history,
    }


@dial_router.post("/autonomy/{product}", summary="Move the dial (two approvers)")
async def set_autonomy(product: str, body: AutonomyChangeRequest) -> dict[str, Any]:
    """docs/08 §6 — two distinct approvers, both heads.

    The change is recorded as an amendment to the pack's autonomy document
    rather than as a new pack: the credit policy has not changed, and re-issuing
    it would make every decision look as though it had.
    """
    current, _, _ = await effective_autonomy(product)
    try:
        check_approvers([a.model_dump() for a in body.approvers])
        amended = amend(current, body.model_dump(exclude_none=True))
    except AmendmentError as exc:
        raise ValidationFailed(str(exc), product=product) from exc

    async with session() as db:
        sequence = await next_amendment_sequence(db, product)
        version = version_for(product, sequence)
        await save_amendment(
            db,
            product_code=product,
            version=version,
            body=amended,
            approved_by=[f"{a.role}:{a.actor_id}" for a in body.approvers],
        )
        await emit(
            db,
            "autonomy.setting_changed",
            {
                "product_code": product,
                "autonomy_version": version,
                "setting": amended.get("setting"),
                "previous_setting": current.get("setting"),
                "approved_by": [a.actor_id for a in body.approvers],
                "reason": body.reason,
            },
            key=product,
            producer="policy",
        )
        await db.commit()

    return {
        "product_code": product,
        "autonomy_version": version,
        "setting": amended.get("setting"),
        "previous_setting": current.get("setting"),
        "approved_by": [{"role": a.role, "actor_id": a.actor_id} for a in body.approvers],
    }


@dial_router.post("/kill-switch/{product}", summary="Stop the platform acting alone")
async def activate_kill_switch(product: str, body: KillSwitchRequest) -> dict[str, Any]:
    """One owner is enough. Needing a second opinion is how a stop gets
    delayed, and stopping is always the safe direction."""
    autonomy, version, _ = await effective_autonomy(product)
    owners = {str(o).upper() for o in (autonomy.get("kill_switch") or {}).get("owners") or []}
    owners = owners or set(KILL_SWITCH_OWNERS)
    if body.actor_role.upper() not in owners:
        raise Forbidden(
            f"{body.actor_role} may not pull the kill switch on {product}",
            owners=sorted(owners),
        )

    async with session() as db:
        await set_kill_switch(db, product, enabled=True, actor=body.actor_id, reason=body.reason)
        await emit(
            db,
            "kill_switch.activated",
            {
                "product_code": product,
                "activated_by": body.actor_id,
                "actor_role": body.actor_role,
                "reason": body.reason,
                "reverts_to": effective_setting(autonomy, kill_switch=True),
            },
            key=product,
            producer="policy",
        )
        await db.commit()
        state = await kill_switch_state(db, product)

    return {
        "product_code": product,
        "autonomy_version": version,
        "kill_switch": state,
        "setting": autonomy.get("setting"),
        "effective_setting": effective_setting(autonomy, kill_switch=True),
    }


@dial_router.delete("/kill-switch/{product}", summary="Release the kill switch")
async def release_kill_switch(product: str, body: KillSwitchRequest) -> dict[str, Any]:
    """Releasing restores the setting the institution chose, because the switch
    overrode it rather than overwriting it."""
    autonomy, version, _ = await effective_autonomy(product)
    owners = {str(o).upper() for o in (autonomy.get("kill_switch") or {}).get("owners") or []}
    owners = owners or set(KILL_SWITCH_OWNERS)
    if body.actor_role.upper() not in owners:
        raise Forbidden(
            f"{body.actor_role} may not release the kill switch on {product}",
            owners=sorted(owners),
        )

    async with session() as db:
        await set_kill_switch(db, product, enabled=False, actor=body.actor_id, reason=body.reason)
        await emit(
            db,
            "kill_switch.released",
            {
                "product_code": product,
                "released_by": body.actor_id,
                "actor_role": body.actor_role,
                "reason": body.reason,
                "restored_setting": autonomy.get("setting"),
            },
            key=product,
            producer="policy",
        )
        await db.commit()
        state = await kill_switch_state(db, product)

    return {
        "product_code": product,
        "autonomy_version": version,
        "kill_switch": state,
        "setting": autonomy.get("setting"),
        "effective_setting": effective_setting(autonomy, kill_switch=False),
    }
