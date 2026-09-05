"""Tool declarations (docs/06 §4).

A tool is the only way an agent reaches data. Agents hold no credentials and no
database access (CLAUDE.md §2.5), so every capability an agent has is a
``ToolSpec`` somebody deliberately wrote down.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = ["EvidenceSpec", "PermittedUse", "SideEffect", "ToolSpec"]


class SideEffect(StrEnum):
    """What a tool may do to the world."""

    NONE = "NONE"
    READ = "READ"
    #: Records a proposal for a human to approve. Never executes anything.
    WRITE_PROPOSAL = "WRITE_PROPOSAL"


class PermittedUse(StrEnum):
    """Purpose limitation carried by evidence and enforced on tool output."""

    UNDERWRITING = "UNDERWRITING"
    SERVICING = "SERVICING"
    COLLECTIONS = "COLLECTIONS"
    FRAUD = "FRAUD"
    ANALYTICS = "ANALYTICS"


@dataclass(frozen=True, slots=True)
class EvidenceSpec:
    """How a tool's output becomes citable evidence (docs/03 §2).

    ``locator_from`` maps EvidenceRef locator keys to dotted paths in the tool's
    output, so a claim can be traced to the exact field it came from.
    """

    type: str
    source_system: str
    #: Dotted path to the list of records, or None when the output is one record.
    items_path: str | None = None
    #: Dotted path to the id of the source record.
    source_record_path: str | None = None
    locator_from: dict[str, str] = field(default_factory=dict)
    value_path: str | None = None
    display_path: str | None = None
    confidence_path: str | None = None
    default_confidence: float = 1.0

    def __post_init__(self) -> None:
        allowed = {
            "DOCUMENT_FIELD",
            "CORE_FIELD",
            "POLICY_RULE",
            "MODEL_OUTPUT",
            "ANALYTIC_RESULT",
            "TIMELINE_EVENT",
            "HUMAN_INPUT",
            "RETRIEVED_CLAUSE",
        }
        if self.type not in allowed:
            raise ValueError(f"unknown evidence type {self.type!r}; allowed: {sorted(allowed)}")


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One entry in the registry."""

    name: str
    version: str
    handler: Callable[..., Any]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    purpose_tags: frozenset[PermittedUse]
    side_effects: SideEffect
    backing_service: str
    evidence: EvidenceSpec | None = None
    #: Dotted output paths masked when the caller's purpose is not in `purpose_tags`
    #: for that field. Keyed by field path -> the purposes that may see it.
    field_purposes: dict[str, frozenset[PermittedUse]] = field(default_factory=dict)
    description: str = ""

    def __post_init__(self) -> None:
        if not self.purpose_tags:
            raise ValueError(f"tool {self.name} declares no purpose tags")
        if self.side_effects is SideEffect.NONE and self.evidence is not None:
            raise ValueError(f"tool {self.name} produces evidence but claims no side effects")

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    def permits(self, purpose: PermittedUse) -> bool:
        return purpose in self.purpose_tags
