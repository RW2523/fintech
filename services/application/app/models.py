"""Request and response models for application-service (docs/08 §1)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateApplication(Strict):
    member_id: str = Field(pattern="^M-[0-9]{6}$")
    product_code: str = Field(pattern="^PF-[A-Z]+$")
    amount: Decimal = Field(gt=0)
    tenor_months: int = Field(ge=6, le=120)
    purpose: str = Field(min_length=1)


class SubmitApplication(Strict):
    """Submitting freezes a snapshot; the document ids fix the bundle version."""

    document_ids: list[str] = Field(default_factory=list)
    actor_id: str = "system"
    actor_role: str = "SYSTEM"
