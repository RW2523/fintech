"""Request shapes for audit-service (docs/04 §6)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActorBody(Strict):
    id: str = Field(min_length=1, max_length=64)
    role: str = Field(default="SYSTEM", max_length=32)
    kind: str = Field(default="SERVICE", max_length=16)


class AuditWrite(Strict):
    """One thing that happened, with the state either side of it."""

    actor: ActorBody
    action: str = Field(min_length=2, max_length=64)
    service: str = Field(min_length=2, max_length=32)
    case_id: str | None = None
    run_id: str | None = None
    object_ref: dict[str, Any] = Field(default_factory=dict)
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    policy_version: str | None = None
    model_versions: dict[str, str] = Field(default_factory=dict)
    trace_id: str | None = None
    ip: str | None = None


class ExportRequest(Strict):
    """Export one day to the write-once bucket. Yesterday by default."""

    day: str | None = None
