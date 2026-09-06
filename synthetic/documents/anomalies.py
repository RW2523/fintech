"""Controlled anomalies (docs/10 §6).

Every anomaly is injected deliberately and recorded in the manifest, so the
forensics and reconciliation checks in T-024 can be scored against a known
answer rather than against a hunch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = ["INJECTED_SHARE", "Anomaly", "AnomalyKind"]


class AnomalyKind(StrEnum):
    """What was done to the document, and the finding it should produce."""

    #: The net pay was raised and re-typeset in a different font (S3).
    EDITED_TOTAL = "EDITED_TOTAL"
    #: The same statement image submitted by two different members (S3).
    REUSED_IMAGE = "REUSED_IMAGE"
    #: Payslip net sits below the salary-deduction record (S2).
    INCOME_VARIANCE = "INCOME_VARIANCE"
    #: PDF creation date precedes the period it claims to cover (INT-01).
    METADATA_MISMATCH = "METADATA_MISMATCH"
    #: Rendered with another employer's layout (INT-07).
    TEMPLATE_MISMATCH = "TEMPLATE_MISMATCH"
    #: Identity details disagree with the member record (INT-08).
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    #: The same id number on two applicants (INT-04).
    DUPLICATE_ID = "DUPLICATE_ID"


#: Reason code each anomaly should surface as (docs/03 §11).
REASON_CODE = {
    AnomalyKind.EDITED_TOTAL: "INT-01",
    AnomalyKind.REUSED_IMAGE: "INT-02",
    AnomalyKind.INCOME_VARIANCE: "INT-03",
    AnomalyKind.METADATA_MISMATCH: "INT-01",
    AnomalyKind.TEMPLATE_MISMATCH: "INT-07",
    AnomalyKind.IDENTITY_MISMATCH: "INT-08",
    AnomalyKind.DUPLICATE_ID: "INT-04",
}

#: Severity the forensics pass should assign (docs/07 §1.4-1.5).
SEVERITY = {
    AnomalyKind.EDITED_TOTAL: "HIGH",
    AnomalyKind.REUSED_IMAGE: "HIGH",
    AnomalyKind.INCOME_VARIANCE: "MEDIUM",
    AnomalyKind.METADATA_MISMATCH: "MEDIUM",
    AnomalyKind.TEMPLATE_MISMATCH: "MEDIUM",
    AnomalyKind.IDENTITY_MISMATCH: "CRITICAL",
    AnomalyKind.DUPLICATE_ID: "HIGH",
}

#: Roughly this share of applications carries at least one injected anomaly.
INJECTED_SHARE = 0.12


@dataclass(frozen=True, slots=True)
class Anomaly:
    """One injected defect, as it appears in the manifest."""

    kind: AnomalyKind
    document_id: str
    application_id: str
    member_id: str
    detail: dict[str, Any]

    def as_row(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "document_id": self.document_id,
            "application_id": self.application_id,
            "member_id": self.member_id,
            "reason_code": REASON_CODE[self.kind],
            "expected_severity": SEVERITY[self.kind],
            "detail": self.detail,
        }
