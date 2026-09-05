"""Request and response models for policy-service (docs/08 §4)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.context import PolicyInputs


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaseInputs(Strict):
    """The flat facts a case is judged on. Assembled by the workflow."""

    member_status: str = "ACTIVE"
    member_tenure_months: int = Field(ge=0)
    member_age: int = Field(ge=0)
    member_total_exposure: Decimal = Field(ge=0)
    member_grade: str = Field(pattern="^[A-E]$")
    member_class: str = "STANDARD"
    identity_verified: bool = True
    identity_mismatch: str | None = None

    requested_amount: Decimal = Field(ge=0)
    requested_tenor: int = Field(ge=0)
    requested_purpose: str = "PERSONAL"

    documents_required_complete: bool = True
    documents_min_critical_confidence: float = Field(default=1.0, ge=0, le=1)
    documents_present: list[str] = Field(default_factory=list)
    document_confidence: dict[str, float] = Field(default_factory=dict)

    income_verified: bool = True
    income_verified_monthly: Decimal = Field(default=Decimal("0"), ge=0)
    income_stability: str = "STABLE"
    income_source_variance: float = 0.0
    commitments_monthly: Decimal = Field(default=Decimal("0"), ge=0)

    fraud_level: str = "NONE"
    fraud_integrity_score: int = Field(default=100, ge=0, le=100)
    history_arrears_12m: int = Field(default=0, ge=0)
    history_late_12m: int = Field(default=0, ge=0)
    history_restructures: int = Field(default=0, ge=0)

    actor_role: str = "CREDIT_OFFICER"
    actor_max_amount: Decimal | None = None

    member_state: str = "STABLE"
    restructure_type: str | None = None

    def to_policy_inputs(self) -> PolicyInputs:
        return PolicyInputs(
            member_status=self.member_status,
            member_tenure_months=self.member_tenure_months,
            member_age=self.member_age,
            member_total_exposure=self.member_total_exposure,
            member_grade=self.member_grade,
            member_class=self.member_class,
            identity_verified=self.identity_verified,
            identity_mismatch=self.identity_mismatch,
            requested_amount=self.requested_amount,
            requested_tenor=self.requested_tenor,
            requested_purpose=self.requested_purpose,
            documents_required_complete=self.documents_required_complete,
            documents_min_critical_confidence=self.documents_min_critical_confidence,
            documents_present=tuple(self.documents_present),
            document_confidence=dict(self.document_confidence),
            income_verified=self.income_verified,
            income_verified_monthly=self.income_verified_monthly,
            income_stability=self.income_stability,
            income_source_variance=self.income_source_variance,
            commitments_monthly=self.commitments_monthly,
            fraud_level=self.fraud_level,
            fraud_integrity_score=self.fraud_integrity_score,
            history_arrears_12m=self.history_arrears_12m,
            history_late_12m=self.history_late_12m,
            history_restructures=self.history_restructures,
            actor_role=self.actor_role,
            actor_max_amount=self.actor_max_amount,
            member_state=self.member_state,
            restructure_type=self.restructure_type,
        )


class EvaluateRequest(Strict):
    product_code: str = "PF-STD"
    policy_version: str | None = Field(
        default=None, description="Defaults to the active version for the product."
    )
    snapshot_id: str | None = None
    inputs: CaseInputs


class AffordabilityRequest(Strict):
    product_code: str = "PF-STD"
    policy_version: str | None = None
    inputs: CaseInputs
    overrides: dict[str, Any] = Field(
        default_factory=dict, description="Only the Challenger's requested substitutions (docs/06 §5.2)."
    )
