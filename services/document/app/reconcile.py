"""Cross-source reconciliation (docs/07 §1.5).

A single document can be internally consistent and still be wrong. These checks
compare what the payslip says against what the employer remitted, what the bank
received, and what the cooperative already holds on the member. Disagreement
between independent sources is the signal.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

from app.forensics import Finding

__all__ = [
    "EMPLOYER_MATCH_THRESHOLD",
    "INCOME_VARIANCE_HIGH",
    "INCOME_VARIANCE_MEDIUM",
    "MemberRecord",
    "duplicate_identities",
    "reconcile",
]

#: docs/07 §1.5 — income variance bands.
INCOME_VARIANCE_MEDIUM = 0.05
INCOME_VARIANCE_HIGH = 0.15

#: Jaro-Winkler similarity above which two employer names are the same employer.
#: docs/07 §1.5 also pairs this with bge-m3 embeddings; the embedding half needs
#: the LLM gateway and arrives with it in T-041.
EMPLOYER_MATCH_THRESHOLD = 92.0
#: Names on documents differ in spacing and case more than in substance.
NAME_MATCH_THRESHOLD = 88.0

#: Below this a figure was not read well enough to accuse anyone with. A
#: document that cannot be read is a DOC-04 problem, not an integrity finding.
MIN_INPUT_CONFIDENCE = 0.75

_MONEY = re.compile(r"^-?[\d,]*\d(\.\d{2})?$")


def _amount(value: Any) -> float | None:
    if value is None or not _MONEY.match(str(value).strip()):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _variance(left: float, right: float) -> float:
    """Relative gap between two figures for the same thing."""
    largest = max(abs(left), abs(right))
    return abs(left - right) / largest if largest else 0.0


@dataclass(frozen=True, slots=True)
class MemberRecord:
    """What the cooperative already knows, for comparison."""

    member_id: str
    name: str | None = None
    id_number: str | None = None
    dob: str | None = None
    employer_name: str | None = None
    #: cycle label -> amount the employer remitted
    deductions: dict[str, float] | None = None
    #: id numbers already on file, keyed by the member they belong to
    id_numbers_on_file: dict[str, str] | None = None


#: payslip field -> the period field naming the cycle it belongs to
_NET_PERIODS = (
    ("net_salary", "period"),
    ("net_salary_prior_1", "period_prior_1"),
    ("net_salary_prior_2", "period_prior_2"),
)


#: A net below this share of gross was misread, not earned: deductions do not
#: consume nearly a whole pay packet. Comparing such a figure against the
#: employer's record produces an accusation about the OCR rather than about
#: the document, so the reading is dropped and the case routes on DOC-04
#: instead. Measured on this corpus, two clean payslips were accused this way.
MIN_NET_SHARE_OF_GROSS = 0.10


def _payslip_nets(fields: dict[str, Any]) -> dict[str, float]:
    """Net pay by cycle, so it is compared against the same cycles.

    A reading that could not have come off this payslip is left out rather
    than compared: the gross is on the same page, and a net that is a sliver
    of it is a failure to read, not a discrepancy to report.
    """
    gross = _amount(fields.get("gross_salary"))
    found: dict[str, float] = {}
    for net_field, period_field in _NET_PERIODS:
        amount = _amount(fields.get(net_field))
        cycle = str(fields.get(period_field) or "").strip()
        if amount is None or not re.match(r"^\d{4}-\d{2}$", cycle):
            continue
        if gross is not None and gross > 0 and not (MIN_NET_SHARE_OF_GROSS <= amount / gross <= 1.0):
            continue
        found[cycle] = amount
    return found


def reconcile(
    *,
    member: MemberRecord,
    payslip: dict[str, Any] | None = None,
    identity: dict[str, Any] | None = None,
    bank_statement: dict[str, Any] | None = None,
    document_ids: dict[str, str] | None = None,
    confidences: dict[str, float] | None = None,
) -> list[Finding]:
    """Compare the case file against itself and against the member record."""
    findings: list[Finding] = []
    ids = document_ids or {}

    def read_well(*names: str) -> bool:
        if not confidences:
            return True
        return all(confidences.get(name, 1.0) >= MIN_INPUT_CONFIDENCE for name in names)

    # --- income across payslip, deduction record and bank -------------------
    if payslip:
        nets = _payslip_nets(payslip)
        # Compare the same cycles: a payslip for March against what the
        # employer reported for March (docs/07 §1.5).
        shared = sorted(set(nets) & set(member.deductions or {}))
        if (
            shared
            and member.deductions
            and read_well("net_salary", "net_salary_prior_1", "net_salary_prior_2")
        ):
            median_net = statistics.median(nets[cycle] for cycle in shared)
            remitted = statistics.median(member.deductions[cycle] for cycle in shared)
            variance = _variance(median_net, remitted)
            if variance > INCOME_VARIANCE_MEDIUM:
                findings.append(
                    Finding(
                        code="INT-03",
                        severity="HIGH" if variance > INCOME_VARIANCE_HIGH else "MEDIUM",
                        document_id=ids.get("payslip"),
                        detail={
                            "check": "income_across_sources",
                            "cycles": shared,
                            "payslip_net_median": round(median_net, 2),
                            "deduction_record_median": round(remitted, 2),
                            "variance": round(variance, 4),
                            "observed": (
                                "the payslip and the employer's deduction record disagree on income"
                            ),
                        },
                    )
                )

        if nets and bank_statement:
            credited = _amount(bank_statement.get("salary_credit"))
            if credited is not None:
                variance = _variance(statistics.median(nets.values()), credited)
                if variance > INCOME_VARIANCE_MEDIUM:
                    findings.append(
                        Finding(
                            code="INT-03",
                            severity="HIGH" if variance > INCOME_VARIANCE_HIGH else "MEDIUM",
                            document_id=ids.get("bank_statement"),
                            detail={
                                "check": "income_against_bank",
                                "payslip_net_median": round(statistics.median(nets.values()), 2),
                                "bank_salary_credit": credited,
                                "variance": round(variance, 4),
                            },
                        )
                    )

    # --- employer across the application and the documents ------------------
    if payslip and member.employer_name:
        stated = str(payslip.get("employer_name") or "")
        if stated:
            similarity = fuzz.WRatio(stated.casefold(), member.employer_name.casefold())
            if similarity < EMPLOYER_MATCH_THRESHOLD:
                findings.append(
                    Finding(
                        code="INT-06",
                        severity="MEDIUM",
                        document_id=ids.get("payslip"),
                        detail={
                            "check": "employer_mismatch",
                            "document_employer": stated,
                            "record_employer": member.employer_name,
                            "similarity": round(similarity, 1),
                        },
                    )
                )

    # --- identity against the member record ---------------------------------
    if identity:
        stated_name = str(identity.get("name") or "")
        if stated_name and member.name and read_well("name"):
            similarity = fuzz.WRatio(stated_name.casefold(), member.name.casefold())
            if similarity < NAME_MATCH_THRESHOLD:
                findings.append(
                    Finding(
                        code="INT-08",
                        severity="CRITICAL",
                        document_id=ids.get("identity"),
                        detail={
                            "check": "identity_name_mismatch",
                            "document_name": stated_name,
                            "record_name": member.name,
                            "similarity": round(similarity, 1),
                            "observed": ("the name on the identity document is not the member's"),
                        },
                    )
                )

        stated_dob = str(identity.get("dob") or "")
        if stated_dob and member.dob and stated_dob != member.dob:
            findings.append(
                Finding(
                    code="INT-08",
                    severity="CRITICAL",
                    document_id=ids.get("identity"),
                    detail={
                        "check": "identity_dob_mismatch",
                        "document_dob": stated_dob,
                        "record_dob": member.dob,
                    },
                )
            )

        # --- the same id number on two members ------------------------------
        stated_id = str(identity.get("id_number") or "")
        for other_member, other_id in (member.id_numbers_on_file or {}).items():
            if other_member == member.member_id:
                continue
            if stated_id and stated_id == other_id:
                findings.append(
                    Finding(
                        code="INT-04",
                        severity="HIGH",
                        document_id=ids.get("identity"),
                        detail={
                            "check": "duplicate_id_number",
                            "id_number": stated_id,
                            "also_held_by": other_member,
                            "observed": ("this identity number is already on file for another member"),
                        },
                    )
                )
                break

    return findings


def duplicate_identities(
    documents: list[dict[str, Any]],
    *,
    max_ocr_distance: int = 1,
) -> list[Finding]:
    """The same identity number across members (docs/07 §1.5, INT-04).

    A portfolio sweep rather than a per-document check: whichever application
    arrived first is as much a party to a duplicate as the one that followed,
    so every sharer is flagged. Numbers are grouped tolerantly, because a single
    mis-read character should not defeat the check.
    """
    readings = [(row, re.sub(r"[^A-Z0-9]", "", str(row.get("id_number") or "").upper())) for row in documents]
    readings = [(row, number) for row, number in readings if number]

    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for row, number in readings:
        for index, (key, _rows) in enumerate(groups):
            same = key == number or (
                len(key) == len(number)
                and sum(a != b for a, b in zip(key, number, strict=True)) <= max_ocr_distance
            )
            if same:
                groups[index][1].append(row)
                break
        else:
            groups.append((number, [row]))

    findings: list[Finding] = []
    for number, rows in groups:
        sharers = {str(r.get("member_id")) for r in rows}
        if len(sharers) < 2:
            continue
        for row in rows:
            findings.append(
                Finding(
                    code="INT-04",
                    severity="HIGH",
                    document_id=row.get("document_id"),
                    detail={
                        "check": "duplicate_id_number",
                        "id_number": number,
                        "held_by": sorted(sharers),
                        "observed": ("this identity number appears on more than one member"),
                    },
                )
            )
    return findings
