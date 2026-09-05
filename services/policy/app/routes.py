"""policy-service endpoints (docs/08 §4)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter

from app.affordability import AffordabilityInputs, compute
from app.evaluate import evaluate_case
from app.models import AffordabilityRequest, EvaluateRequest
from app.packs import PackError, PolicyPack, available_packs, load_pack
from cio_common.errors import NotFound, ValidationFailed

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
