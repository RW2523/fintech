"""Response models for the core-stub façade (docs/08 §9).

Money is a string everywhere it crosses the wire (CLAUDE.md §7).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

Money = Annotated[Decimal, PlainSerializer(lambda v: f"{v:.2f}", return_type=str)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class Employer(Strict):
    employer_id: str
    name: str
    sector: str
    template_id: str | None = None
    deduction_day: int | None = None


class Member(Strict):
    member_id: str
    name_token: str
    dob: date | None = None
    joined_at: date
    status: str
    branch_id: str | None = None
    employer_id: str | None = None
    salary_monthly: Money | None = None
    identity_verified: bool
    contact_updated_at: datetime | None = None
    language: str


class Account(Strict):
    account_id: str
    member_id: str
    product_code: str
    principal: Money
    profit_rate: Decimal
    tenor_months: int
    instalment: Money
    due_day: int | None = None
    opened_at: date
    status: str
    restructured_at: date | None = None


class ScheduleRow(Strict):
    schedule_id: str
    account_id: str
    seq: int
    due_date: date
    amount_due: Money


class Payment(Strict):
    payment_id: str
    schedule_id: str
    paid_at: datetime
    amount_paid: Money
    channel: str | None = None
    reversed: bool


class Deduction(Strict):
    deduction_id: str
    member_id: str
    employer_id: str
    cycle: str
    expected_amount: Money
    received_amount: Money | None = None
    received_at: datetime | None = None


class SavingsPoint(Strict):
    member_id: str
    as_of: date
    balance: Money


class SharePoint(Strict):
    member_id: str
    as_of: date
    units: int
    value: Money


class Guarantor(Strict):
    account_id: str
    guarantor_member_id: str
    since: date


class Bureau(Strict):
    member_id: str
    grade: str
    adverse_flags: list[str]
    as_of: date


class OutageWindow(Strict):
    system: str
    from_ts: datetime
    to_ts: datetime


class Arrangement(Strict):
    arrangement_id: str
    account_id: str
    type: str
    from_date: date
    to_date: date | None = None


class Change(Strict):
    seq: int
    table_name: str
    pk: str
    op: str
    at: datetime


class ActivateRequest(BaseModel):
    """Only execution-service calls this, and only with an approval token."""

    model_config = ConfigDict(extra="forbid")

    member_id: str
    product_code: str
    amount: Decimal = Field(gt=0)
    tenor_months: int = Field(ge=6, le=120)
    instalment: Decimal = Field(gt=0)
    profit_rate: Decimal = Field(ge=0)
    due_day: int = Field(default=1, ge=1, le=28)
    idempotency_key: str = Field(min_length=8)


class StatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    status: str
    idempotency_key: str = Field(min_length=8)


class WriteResult(Strict):
    action: str
    account_id: str | None = None
    status: str
    replayed: bool = False
    core_refs: dict[str, Any] = Field(default_factory=dict)


class BulkRequest(BaseModel):
    """Seeding only. Refused unless CIO_ENV is a non-production environment."""

    model_config = ConfigDict(extra="forbid")

    table: str
    rows: list[dict[str, Any]]
