"""Request and response models for document-service (docs/08 §2)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UploadRequest(Strict):
    """Ask for somewhere to put a file."""

    filename: str = Field(min_length=1, max_length=255)
    mime: str
    size: int = Field(gt=0)
    member_id: str | None = None
    #: What the member says it is. The classifier decides what it actually is.
    declared_type: str | None = None


class CompleteRequest(Strict):
    """The bytes are in place; process them."""

    sha256: str | None = None


class ReviewRequest(Strict):
    """A human correcting an extracted field (docs/08 §2)."""

    field: str = Field(min_length=1)
    value: str
    note: str = Field(default="", max_length=500)
    actor_id: str = Field(min_length=1)


class FieldOut(Strict):
    field: str
    value: str | None
    norm_value: Any = None
    conf: float
    page: int = 1
    bbox: list[float] | None = None
    method: str
    evidence_id: str | None = None
