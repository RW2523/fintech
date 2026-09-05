"""Request models for decision-service (docs/08 §6)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecommendationRequest(Strict):
    """Append a DecisionRecord to the ledger (internal, from the committee)."""

    decision_record: dict[str, Any]
    case_id: str | None = None
    member_id: str | None = None


class OverrideReason(Strict):
    code: str = Field(pattern="^OVR-(0[1-9]|1[0-2])$")
    text: str = Field(min_length=20)


class HumanDecisionRequest(Strict):
    """A person's decision on a case (docs/03 §7)."""

    decision_record_id: str
    case_id: str
    actor_id: str = Field(min_length=1)
    role: str
    final_action: str = Field(
        pattern="^(APPROVE|DECLINE|REQUEST_INFO|ESCALATE|DEFER|APPROVE_WITH_CONDITIONS)$"
    )
    conditions: list[str] = Field(default_factory=list)
    override: bool = False
    override_reason: OverrideReason | None = None
    evidence_acknowledged: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _override_needs_a_reason(self) -> HumanDecisionRequest:
        """docs/03 §7 — required exactly when overriding, and OVR-12 needs more."""
        if self.override and self.override_reason is None:
            raise ValueError("override_reason is required when override is true")
        if not self.override and self.override_reason is not None:
            raise ValueError("override_reason is only allowed when override is true")
        if (
            self.override_reason
            and self.override_reason.code == "OVR-12"
            and len(self.override_reason.text) < 60
        ):
            raise ValueError("OVR-12 requires at least 60 characters of explanation")
        return self


class TokenRequest(Strict):
    """Issue an ApprovalToken (internal, from the workflow)."""

    action_id: str
    decision_record_id: str
    case_id: str
    member_id: str
    product_code: str
    max_amount: Decimal = Field(ge=0)
    idempotency_key: str = Field(min_length=8)
    human_decision_id: str | None = None
    issued_to: dict[str, Any] | None = None
    ttl_seconds: int = Field(default=86400, ge=60, le=86400)
