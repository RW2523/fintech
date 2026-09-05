"""Build the flat rule context and its evidence (docs/05 §1, §3.1).

Rules read dotted keys. Each key carries provenance so the engine can attach an
EvidenceRef for exactly the inputs a rule touched, rather than for the whole
case. A key with no value is simply absent: rules over absent data fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from cio_common.ids import new_id

__all__ = ["EVIDENCE_SOURCES", "PolicyInputs", "build_context", "evidence_for"]


@dataclass(frozen=True, slots=True)
class PolicyInputs:
    """Everything policy-service needs to evaluate a case.

    Assembled by the workflow from the snapshot, the member projection, document
    extraction, the feature snapshot and the model services.
    """

    # member projection
    member_status: str
    member_tenure_months: int
    member_age: int
    member_total_exposure: Decimal
    member_grade: str
    member_class: str = "STANDARD"
    identity_verified: bool = True
    identity_mismatch: str | None = None

    # the request
    requested_amount: Decimal = Decimal("0")
    requested_tenor: int = 0
    requested_purpose: str = "PERSONAL"

    # documents
    documents_required_complete: bool = True
    documents_min_critical_confidence: float = 1.0
    documents_present: tuple[str, ...] = ()
    document_confidence: dict[str, float] | None = None

    # income and commitments
    income_verified: bool = True
    income_verified_monthly: Decimal = Decimal("0")
    income_stability: str = "STABLE"
    income_source_variance: float = 0.0
    commitments_monthly: Decimal = Decimal("0")

    # model outputs
    fraud_level: str = "NONE"
    fraud_integrity_score: int = 100
    history_arrears_12m: int = 0
    history_late_12m: int = 0
    history_restructures: int = 0

    # who is asking
    actor_role: str = "CREDIT_OFFICER"
    actor_max_amount: Decimal | None = None

    # servicing only
    member_state: str = "STABLE"
    restructure_type: str | None = None


#: key prefix -> (EvidenceRef.type, source_system). docs/03 §2.
EVIDENCE_SOURCES: dict[str, tuple[str, str]] = {
    "member": ("CORE_FIELD", "core.member"),
    "identity": ("DOCUMENT_FIELD", "document-service"),
    "documents": ("DOCUMENT_FIELD", "document-service"),
    "income": ("DOCUMENT_FIELD", "document-service"),
    "commitments": ("CORE_FIELD", "core.financing"),
    "requested": ("HUMAN_INPUT", "application-service"),
    "proposed": ("ANALYTIC_RESULT", "policy-service"),
    "affordability": ("ANALYTIC_RESULT", "policy-service"),
    "exposure": ("ANALYTIC_RESULT", "policy-service"),
    "fraud": ("MODEL_OUTPUT", "fraud-service"),
    "history": ("CORE_FIELD", "core.financing"),
    "product": ("POLICY_RULE", "policy-service"),
    "actor": ("HUMAN_INPUT", "gateway"),
    "restructure": ("HUMAN_INPUT", "application-service"),
}


def build_context(
    inputs: PolicyInputs,
    pack_policy: dict[str, Any],
    *,
    affordability: dict[str, Any] | None = None,
    exposure_limit: Decimal | None = None,
) -> dict[str, Any]:
    """The flat context every rule is evaluated against."""
    terms = pack_policy["product_terms"]

    context: dict[str, Any] = {
        "member.status": inputs.member_status,
        "member.tenure_months": inputs.member_tenure_months,
        "member.age": inputs.member_age,
        "member.class": inputs.member_class,
        "member.total_exposure": float(inputs.member_total_exposure),
        "member.grade": inputs.member_grade,
        "member.state": inputs.member_state,
        "identity.verified": inputs.identity_verified,
        "identity.mismatch": inputs.identity_mismatch,
        "documents.required_complete": inputs.documents_required_complete,
        "documents.min_critical_confidence": inputs.documents_min_critical_confidence,
        "income.verified": inputs.income_verified,
        "income.verified_monthly": float(inputs.income_verified_monthly),
        "income.stability": inputs.income_stability,
        "income.source_variance": inputs.income_source_variance,
        "commitments.monthly": float(inputs.commitments_monthly),
        "requested.amount": float(inputs.requested_amount),
        "requested.tenor": inputs.requested_tenor,
        "requested.purpose": inputs.requested_purpose,
        "fraud.level": inputs.fraud_level,
        "fraud.integrity_score": inputs.fraud_integrity_score,
        "history.arrears_12m": inputs.history_arrears_12m,
        "history.late_12m": inputs.history_late_12m,
        "history.restructures": inputs.history_restructures,
        "actor.role": inputs.actor_role,
        "product.min_amount": terms["min_amount"],
        "product.max_amount": terms["max_amount"],
        "product.min_tenor": terms["min_tenor"],
        "product.max_tenor": terms["max_tenor"],
        "product.purposes_allowed": terms["purposes_allowed"],
        "product.structure": terms.get("structure"),
        "affordability.dsr_limit": pack_policy["affordability"]["dsr_limit"],
        "affordability.residual_income_min": pack_policy["affordability"]["residual_income_min"],
    }

    if inputs.actor_max_amount is not None:
        context["actor.max_amount"] = float(inputs.actor_max_amount)
    if inputs.restructure_type is not None:
        context["restructure.type"] = inputs.restructure_type
    if exposure_limit is not None:
        context["exposure.limit"] = float(exposure_limit)

    for document in inputs.documents_present:
        context[f"documents.{document}.present"] = True
    for document, confidence in (inputs.document_confidence or {}).items():
        context[f"documents.{document}.confidence"] = confidence

    if affordability:
        context["affordability.dsr"] = affordability["dsr"]
        context["affordability.stress"] = affordability["stress"]
        context["proposed.instalment"] = float(affordability["instalment"])

    return {k: v for k, v in context.items() if v is not None}


def evidence_for(keys: list[str], context: dict[str, Any], version: str) -> list[dict[str, Any]]:
    """One EvidenceRef per context key a rule read (docs/05 §3.1)."""
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    refs: list[dict[str, Any]] = []

    for key in keys:
        if key not in context:
            continue
        prefix = key.split(".", 1)[0]
        kind, source = EVIDENCE_SOURCES.get(prefix, ("ANALYTIC_RESULT", "policy-service"))
        refs.append(
            {
                "schema": "evidence_ref/1.0",
                "evidence_id": new_id("ev"),
                "type": kind,
                "source_system": source,
                "source_record_id": key,
                "locator": {"field_path": key},
                "value": context[key],
                "display": f"{key} = {context[key]}",
                "confidence": 1.0,
                "captured_at": now,
                "version": version,
                "permitted_uses": ["UNDERWRITING"],
            }
        )
    return refs
