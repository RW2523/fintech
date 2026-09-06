"""The fraud rules (docs/07 §3).

Each rule turns an observation into a finding under an approved reason code.
They are deliberately plain: a rule an officer cannot restate in a sentence is
a rule they cannot defend to the member it affects.

Thresholds live at the top, each with the reason it is where it is.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.findings import Finding
from app.graph import (
    CONCENTRATION_HOLDERS,
    CONCENTRATION_SHARE,
    SERIAL_GUARANTOR_DEGREE,
    EntityGraph,
)

__all__ = [
    "ANOMALY_THRESHOLD",
    "CONTACT_CHANGE_DAYS",
    "VELOCITY_COUNT",
    "VELOCITY_DAYS",
    "CaseFacts",
    "evaluate_rules",
]

#: docs/07 §3 — three or more applications from one employer and branch inside
#: a week. Below that it is a payday, not a pattern.
VELOCITY_COUNT = 3
VELOCITY_DAYS = 7

#: An absolute count says nothing on its own. A branch that always sends three
#: applications a week is not doing anything, and a branch that has never sent
#: more than one is doing something new. Measured on this population the
#: absolute threshold alone raised velocity findings on 14 of 598 clean cases,
#: which by itself breaks the 3% ceiling docs/00 sets for T-033. The burst has
#: to stand out against that employer and branch's own rate.
VELOCITY_MULTIPLE = 3.0

#: docs/07 §3 — a contact detail changed just before applying is worth noting,
#: not worth acting on, which is why it is LOW.
CONTACT_CHANGE_DAYS = 14

#: docs/07 §3 — the isolation forest is advisory. It marks a case as unlike
#: the others, which is a reason to look rather than a reason to doubt.
ANOMALY_THRESHOLD = 0.7

#: A guarantee cycle on its own is ordinary in a cooperative: members stand
#: behind each other, and measured on this population 4.4% of members sit in
#: one. What separates a ring is money moving through the loop at the same
#: time, so a quiet cycle is reported as a graph observation and only a cycle
#: with concurrent borrowing becomes a finding.
CYCLE_MIN_APPLICATIONS = 2


@dataclass(frozen=True, slots=True)
class CaseFacts:
    """Everything the rules need about one case.

    Assembled by the caller from the services that own each part, so the rules
    themselves are pure and can be read, tested and argued about on their own.
    """

    case_id: str
    member_id: str
    application_id: str | None = None
    applied_at: date | None = None
    employer_id: str | None = None
    branch_id: str | None = None
    contact_updated_at: date | None = None
    #: Findings the document service already raised for this case.
    document_findings: tuple[dict[str, Any], ...] = ()
    #: (member_id, employer_id, branch_id, applied_at) for recent applications.
    recent_applications: tuple[dict[str, Any], ...] = ()
    #: This employer and branch's usual applications per velocity window.
    employer_baseline: float | None = None
    #: Application dates by member, for reading a cycle.
    applications_by_member: dict[str, date] | None = None
    #: Members sharing an identity number with this one.
    duplicate_identities: tuple[str, ...] = ()
    #: Guarantees by branch, for the concentration note.
    branch_guarantees: dict[str, list[str]] | None = None
    anomaly_score: float | None = None


# ---------------------------------------------------------------------------
# application-shaped rules
# ---------------------------------------------------------------------------
def velocity(facts: CaseFacts) -> list[Finding]:
    """Several applications from one employer and branch inside a week."""
    if facts.applied_at is None or not facts.employer_id:
        return []
    window_start = facts.applied_at - timedelta(days=VELOCITY_DAYS)
    peers = [
        row
        for row in facts.recent_applications
        if row.get("employer_id") == facts.employer_id
        and row.get("branch_id") == facts.branch_id
        and window_start <= _as_date(row.get("applied_at")) <= facts.applied_at
    ]
    if len(peers) < VELOCITY_COUNT:
        return []
    baseline = facts.employer_baseline
    expected = max(float(VELOCITY_COUNT), VELOCITY_MULTIPLE * baseline if baseline else 0.0)
    if len(peers) < expected:
        # Busy for this branch, but not busier than this branch usually is.
        return []
    return [
        Finding(
            code="INT-04",
            severity="MEDIUM",
            rule="velocity",
            detail={
                "applications": len(peers),
                "window_days": VELOCITY_DAYS,
                "employer_id": facts.employer_id,
                "branch_id": facts.branch_id,
                "usual_per_window": round(baseline, 2) if baseline else None,
                "multiple_required": VELOCITY_MULTIPLE,
                "from": window_start.isoformat(),
                "to": facts.applied_at.isoformat(),
            },
            members=tuple(sorted({str(r["member_id"]) for r in peers if r.get("member_id")})),
        )
    ]


def contact_change(facts: CaseFacts) -> list[Finding]:
    """A contact detail changed shortly before the application."""
    if facts.applied_at is None or facts.contact_updated_at is None:
        return []
    days = (facts.applied_at - facts.contact_updated_at).days
    if not 0 <= days <= CONTACT_CHANGE_DAYS:
        return []
    return [
        Finding(
            code="INT-06",
            severity="LOW",
            rule="contact_change",
            detail={"days_before_application": days, "changed_at": facts.contact_updated_at.isoformat()},
            members=(facts.member_id,),
        )
    ]


def duplicate_applicant(facts: CaseFacts) -> list[Finding]:
    """The same identity presented under more than one membership."""
    others = tuple(sorted(m for m in facts.duplicate_identities if m != facts.member_id))
    if not others:
        return []
    return [
        Finding(
            code="INT-04",
            severity="HIGH",
            rule="duplicate_applicant",
            detail={"also_used_by": list(others)},
            members=(facts.member_id, *others),
        )
    ]


# ---------------------------------------------------------------------------
# document-derived rules
# ---------------------------------------------------------------------------
#: Document findings the fraud service escalates, and to what. The document
#: service says what it saw; the fraud service says what it means for the case.
DOCUMENT_ESCALATION = {
    "INT-02": ("reused_image", "HIGH"),
    "INT-08": ("identity_mismatch", "CRITICAL"),
    "INT-01": ("document_metadata", "MEDIUM"),
    "INT-07": ("template_mismatch", "MEDIUM"),
    "INT-03": ("income_variance", "MEDIUM"),
}


def from_documents(facts: CaseFacts) -> list[Finding]:
    """Carry the document service's findings into the case assessment."""
    out: list[Finding] = []
    for raw in facts.document_findings:
        code = str(raw.get("code", ""))
        if code not in DOCUMENT_ESCALATION:
            continue
        rule, severity = DOCUMENT_ESCALATION[code]
        # An unconfirmed image match is a lead, not an accusation: the document
        # service already graded it, and a weaker grade is never raised here.
        if str(raw.get("severity", "")).upper() == "LOW" and code == "INT-02":
            severity = "LOW"
        document_id = raw.get("document_id")
        out.append(
            Finding(
                code=code,
                severity=severity,
                rule=rule,
                detail=dict(raw.get("detail") or {}),
                members=(facts.member_id,),
                documents=(str(document_id),) if document_id else (),
                evidence_refs=tuple(raw.get("evidence_refs") or ()),
            )
        )
    return out


# ---------------------------------------------------------------------------
# graph rules
# ---------------------------------------------------------------------------
def guarantor_findings(facts: CaseFacts, graph: EntityGraph) -> list[Finding]:
    """Cycles, serial guarantors and branch concentration."""
    out: list[Finding] = []
    applications = facts.applications_by_member or {}

    for cycle in graph.cycles(applications=applications):
        if facts.member_id not in cycle.members:
            continue
        if cycle.concurrent < CYCLE_MIN_APPLICATIONS:
            # A quiet cycle is how a cooperative works, and so is one where the
            # members borrowed years apart. docs/07 §3 uses the ninety-day
            # window to separate HIGH from MEDIUM; measured here, without it as
            # the bar for raising anything at all, incidental cycles alone put
            # clean cases over the 3% ceiling.
            continue
        out.append(
            Finding(
                code="INT-05",
                severity="HIGH" if cycle.is_hot else "MEDIUM",
                rule="guarantor_cycle",
                detail={
                    "cycle_length": cycle.length,
                    "applications_in_cycle": len(cycle.applications),
                    "concurrent_applications": cycle.concurrent,
                    "window_days": cycle.window_days,
                },
                members=cycle.members,
            )
        )

    for member_id, degree in graph.serial_guarantors():
        if member_id != facts.member_id:
            continue
        out.append(
            Finding(
                code="INT-05",
                severity="MEDIUM",
                rule="serial_guarantor",
                detail={"guarantees": degree, "threshold": SERIAL_GUARANTOR_DEGREE},
                members=(member_id,),
            )
        )

    for note in graph.concentration(facts.branch_guarantees or {}):
        if facts.branch_id and note["branch_id"] != facts.branch_id:
            continue
        out.append(
            Finding(
                code="CND-01",
                severity="LOW",
                rule="guarantee_concentration",
                detail={
                    **note,
                    "share_threshold": CONCENTRATION_SHARE,
                    "holders_threshold": CONCENTRATION_HOLDERS,
                },
                members=tuple(note["holders"]),
                advisory=True,
            )
        )
    return out


# ---------------------------------------------------------------------------
# anomaly
# ---------------------------------------------------------------------------
def anomaly(facts: CaseFacts) -> list[Finding]:
    """An advisory note that this case is unlike the ones the model has seen."""
    score = facts.anomaly_score
    if score is None or score < ANOMALY_THRESHOLD:
        return []
    return [
        Finding(
            code="INT-01",
            severity="LOW",
            rule="anomaly",
            detail={
                "score": round(float(score), 4),
                "threshold": ANOMALY_THRESHOLD,
                "meaning": "unlike the training population; a reason to look, not a reason to doubt",
            },
            members=(facts.member_id,),
            advisory=True,
        )
    ]


def evaluate_rules(facts: CaseFacts, graph: EntityGraph) -> list[Finding]:
    """Every rule, in a fixed order, deduplicated by what it found."""
    produced: list[Finding] = []
    for rule in (velocity, contact_change, duplicate_applicant, from_documents, anomaly):
        produced.extend(rule(facts))
    produced.extend(guarantor_findings(facts, graph))

    seen: set[str] = set()
    unique: list[Finding] = []
    for finding in produced:
        if finding.finding_id in seen:
            continue
        seen.add(finding.finding_id)
        unique.append(finding)
    return unique


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def branch_guarantee_index(rows: Sequence[dict[str, Any]]) -> dict[str, list[str]]:
    """Guarantor ids per branch, for the concentration note."""
    index: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        branch = row.get("branch_id")
        guarantor = row.get("guarantor_member_id")
        if branch and guarantor:
            index[str(branch)].append(str(guarantor))
    return dict(index)


def counter_of(values: Sequence[str]) -> Counter[str]:
    return Counter(values)
