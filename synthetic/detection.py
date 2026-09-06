"""Scoring forensics and reconciliation against the injected manifest (T-024).

The corpus knows exactly which documents were tampered with, so detection is
measured, not asserted. The acceptance is strict: every injected anomaly found
at MEDIUM or worse, and no more than 3 % of clean documents raising a finding.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

__all__ = ["DetectionReport", "measure_detection"]

_ROOT = Path(__file__).resolve().parents[1]
_SEVERITY_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


@dataclass
class DetectionReport:
    injected: int = 0
    detected: int = 0
    clean_documents: int = 0
    clean_with_findings: int = 0
    #: Documents whose critical fields could not be read confidently. These
    #: route to a human for more information (DOC-04) rather than accusing
    #: anyone, so they are reported separately from integrity false positives.
    unreadable: int = 0
    by_kind: dict[str, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))
    missed: list[dict[str, Any]] = field(default_factory=list)
    false_positives: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        def rate(hit: int, total: int) -> float:
            return round(hit / total, 4) if total else 0.0

        return {
            "injected": self.injected,
            "detected": self.detected,
            "detection_rate": rate(self.detected, self.injected),
            "clean_documents": self.clean_documents,
            "clean_with_findings": self.clean_with_findings,
            "false_positive_rate": rate(self.clean_with_findings, self.clean_documents),
            "unreadable": self.unreadable,
            "unreadable_rate": rate(self.unreadable, self.clean_documents),
            "by_kind": {
                kind: {"detected": hit, "injected": total, "rate": rate(hit, total)}
                for kind, (hit, total) in sorted(self.by_kind.items())
            },
            "missed": self.missed[:20],
            "sample_false_positives": self.false_positives[:10],
        }

    @property
    def meets_thresholds(self) -> bool:
        report = self.as_dict()
        return report["detection_rate"] >= 1.0 and report["false_positive_rate"] <= 0.03


#: Which finding codes count as detecting each injected anomaly.
_EXPECTED_CODES = {
    # An inflated net breaks the page's own arithmetic (INT-01) and disagrees
    # with the employer's reported net (INT-03). Either is a correct catch.
    "EDITED_TOTAL": {"INT-01", "INT-03"},
    "REUSED_IMAGE": {"INT-02"},
    "INCOME_VARIANCE": {"INT-03"},
    "METADATA_MISMATCH": {"INT-01"},
    "TEMPLATE_MISMATCH": {"INT-07"},
    "IDENTITY_MISMATCH": {"INT-08"},
    "DUPLICATE_ID": {"INT-04"},
}


def measure_detection(corpus: Path, *, limit: int | None = None) -> DetectionReport:
    """Run the checks over the corpus and compare with the manifest."""
    sys.path.insert(0, str(_ROOT / "services" / "document"))
    from PIL import Image

    from app.extract import extract_fields
    from app.forensics import (
        check_arithmetic,
        check_layout_signature,
        check_metadata,
        check_readability,
        check_reused_image,
    )
    from app.ocr import read_lines
    from app.reconcile import MemberRecord, duplicate_identities, reconcile

    documents = [json.loads(line) for line in (corpus / "documents.jsonl").read_text().splitlines()]
    anomalies = json.loads((corpus / "anomalies.json").read_text())
    by_document: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in anomalies:
        by_document[row["document_id"]].append(row)

    population = corpus.parent
    members = {
        row["member_id"]: row
        for row in (json.loads(line) for line in (population / "member.jsonl").read_text().splitlines())
    }
    employers = {
        row["employer_id"]: row
        for row in (json.loads(line) for line in (population / "employer.jsonl").read_text().splitlines())
    }
    deductions: dict[str, dict[str, float]] = defaultdict(dict)
    for line in (population / "deduction.jsonl").read_text().splitlines():
        row = json.loads(line)
        # the employer's reported net pay, which is what a payslip is compared
        # against (docs/07 §1.5); the deducted amount is a different figure
        if row.get("net_salary") is not None:
            deductions[row["member_id"]][row["cycle"]] = float(row["net_salary"])

    if limit:
        # keep every anomalous document, then fill up with clean ones
        anomalous = [d for d in documents if d["document_id"] in by_document]
        clean = [d for d in documents if d["document_id"] not in by_document]
        documents = anomalous + clean[: max(0, limit - len(anomalous))]

    application_dates = {
        row["application_id"]: date.fromisoformat(row["created_at"])
        for row in (json.loads(line) for line in (corpus / "applications.jsonl").read_text().splitlines())
    }

    # identity readings, for the portfolio-level duplicate sweep
    identities: list[dict[str, Any]] = []
    seen: list[dict[str, Any]] = []
    report = DetectionReport()

    per_document: dict[str, list[Any]] = {}

    for document in documents:
        lines = read_lines(Image.open(corpus / "files" / document["filename"]))
        extracted = extract_fields(document["type"], lines)
        fields = {f.name: f.value for f in extracted}
        confidences = {f.name: f.confidence for f in extracted}
        member = members.get(document["member_id"], {})
        employer = employers.get(member.get("employer_id", ""), {})

        findings = []
        findings += check_arithmetic(fields, document_id=document["document_id"], confidences=confidences)
        findings += check_metadata(
            fields,
            document_id=document["document_id"],
            application_date=application_dates.get(document["application_id"]),
        )
        findings += check_readability(
            document["type"],
            fields,
            confidences,
            document_id=document["document_id"],
        )
        findings += check_reused_image({**document, **fields}, seen, content_key="account_holder")

        if document["type"] == "PAYSLIP_LATEST_3":
            findings += check_layout_signature(
                expected_template=employer.get("template_id"),
                observed_template=document.get("template_id"),
                document_id=document["document_id"],
            )

        record = MemberRecord(
            member_id=document["member_id"],
            name=member.get("name_token"),
            dob=member.get("dob"),
            employer_name=employer.get("name"),
            deductions=deductions.get(document["member_id"]),
        )
        findings += reconcile(
            member=record,
            payslip=fields if document["type"] == "PAYSLIP_LATEST_3" else None,
            identity=fields if document["type"] == "IDENTITY" else None,
            document_ids={"payslip": document["document_id"], "identity": document["document_id"]},
        )

        if document["type"] == "IDENTITY":
            identities.append({**document, **fields})
        seen.append({**document, **fields})

        per_document[document["document_id"]] = findings

    # The duplicate-identity sweep is portfolio wide: whichever application
    # arrived first is as much a party to a duplicate as the one that followed.
    for finding in duplicate_identities(identities):
        per_document.setdefault(finding.document_id or "", []).append(finding)

    # Image reuse is a portfolio sweep: a copy submitted before its original
    # is still a copy, so the comparison cannot be limited to what came first.
    for candidate in seen:
        for finding in check_reused_image(candidate, seen, content_key="account_holder"):
            per_document.setdefault(finding.document_id or "", []).append(finding)

    # A reuse or a duplicate implicates two documents. The manifest names the
    # one that was tampered with; its partner is legitimately flagged too, so
    # counting that as a false positive would penalise correct behaviour.
    implicated: set[str] = set()
    for found in per_document.values():
        for finding in found:
            if finding.code not in ("INT-02", "INT-04"):
                continue
            partner = finding.detail.get("other_document_id")
            if partner:
                implicated.add(str(partner))
            if finding.code == "INT-04":
                sharers = set(finding.detail.get("held_by", []))
                implicated.update(
                    d["document_id"]
                    for d in documents
                    if d["member_id"] in sharers and d["type"] == "IDENTITY"
                )

    for document in documents:
        findings = per_document.get(document["document_id"], [])
        # MEDIUM or worse. DOC-04 is held apart: an unreadable document asks
        # for more information, it does not accuse anyone of anything.
        codes = {f.code for f in findings if _SEVERITY_ORDER.index(f.severity) >= 1 and f.code != "DOC-04"}
        unreadable = any(f.code == "DOC-04" for f in findings)
        injected_here = by_document.get(document["document_id"], [])
        if injected_here:
            for row in injected_here:
                kind = row["kind"]
                report.injected += 1
                report.by_kind[kind][1] += 1
                if codes & _EXPECTED_CODES.get(kind, set()):
                    report.detected += 1
                    report.by_kind[kind][0] += 1
                else:
                    report.missed.append(
                        {
                            "document_id": document["document_id"],
                            "kind": kind,
                            "type": document["type"],
                            "expected_codes": sorted(_EXPECTED_CODES.get(kind, set())),
                            "raised": sorted(codes),
                        }
                    )
        elif document["document_id"] in implicated:
            # correctly flagged as the other half of an injected pair
            pass
        else:
            report.clean_documents += 1
            report.unreadable += int(unreadable)
            if codes:
                report.clean_with_findings += 1
                report.false_positives.append(
                    {
                        "document_id": document["document_id"],
                        "type": document["type"],
                        "raised": sorted(codes),
                        "detail": [f.detail for f in findings][:1],
                    }
                )

    return report
