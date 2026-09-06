"""What a fraud finding is, and how severe (docs/07 §3, docs/03 §11).

A finding says what was observed and under which approved reason code. It
never says the applicant did anything: an observation is evidence for a person
to weigh, and CRITICAL is the only level that stops a case by itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cio_common.hashing import canonical_json, sha256
from cio_common.ids import derived_id

__all__ = ["SEVERITY_ORDER", "Finding", "worst_severity"]

#: Ordered weakest to strongest. A finding never carries a severity outside it.
SEVERITY_ORDER = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

_RANK = {name: index for index, name in enumerate(SEVERITY_ORDER)}


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing the rules noticed."""

    code: str
    severity: str
    rule: str
    detail: dict[str, Any] = field(default_factory=dict)
    members: tuple[str, ...] = ()
    documents: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    #: An advisory finding informs but never raises the case level on its own.
    advisory: bool = False

    def __post_init__(self) -> None:
        if self.severity not in _RANK:
            raise ValueError(
                f"unknown severity {self.severity!r}; expected one of {', '.join(SEVERITY_ORDER)}"
            )

    @property
    def finding_id(self) -> str:
        """Derived from what was found, so re-assessing does not multiply it."""
        return derived_id(
            "fnd",
            self.rule,
            self.code,
            canonical_json(
                {"members": sorted(self.members), "documents": sorted(self.documents), "detail": self.detail}
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "code": self.code,
            "severity": self.severity,
            "rule": self.rule,
            "detail": self.detail,
            "members": list(self.members),
            "documents": list(self.documents),
            "evidence_refs": list(self.evidence_refs),
            "advisory": self.advisory,
            "detail_digest": sha256(canonical_json(self.detail))[:16],
        }


def worst_severity(findings: list[Finding]) -> str:
    """The level of a case: its strongest non-advisory finding.

    An advisory finding is deliberately excluded. An anomaly score says a case
    is unusual, and unusual is a reason to look, not a reason to accuse
    (docs/07 §3).
    """
    counted = [f for f in findings if not f.advisory]
    if not counted:
        return "LOW"
    return max(counted, key=lambda f: _RANK[f.severity]).severity
