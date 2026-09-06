"""Request shapes for execution-service (docs/08 §7)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionProposalRequest(Strict):
    """A proposal, from the committee or an agent. Not an instruction."""

    action_id: str = Field(min_length=3, max_length=64)
    level: str = Field(pattern="^L[0-3]$")
    type: str = Field(min_length=3, max_length=48)
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: dict[str, Any] = Field(default_factory=dict)
    requires: str = Field(default="OFFICER", max_length=24)
    proposed_by: str = Field(min_length=1, max_length=64)
    case_id: str | None = None
    member_id: str | None = None
    decision_record_id: str | None = None
    human_decision_id: str | None = None


class ExecuteRequest(Strict):
    """Carry out an approved action."""

    token_id: str = Field(min_length=3, max_length=64)
    #: The caller's own key. Defaults to one derived from the action, so a
    #: caller that omits it still cannot execute the same action twice.
    idempotency_key: str | None = Field(default=None, max_length=128)
    product_code: str = "PF-STD"
