"""Assessing a case (docs/07 §3).

Gather the facts, build the graph, run the rules, grade the result, and hand
back the neighbourhood a person needs to see to judge it. The integrity score
comes from the shared Decision Factor formula, so the number the policy engine
consumes is the number this service produced.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from cio_dff.factors import integrity_score

from app.anomaly import score_case
from app.facts import CaseBundle, FactSource
from app.findings import Finding, worst_severity
from app.graph import EntityGraph
from app.rules import evaluate_rules
from cio_common.hashing import canonical_json, sha256
from cio_common.ids import derived_id

__all__ = ["RULES_VERSION", "Assessment", "assess"]

#: Bumped when a rule or a threshold changes, so an old assessment stays
#: readable as the product of the rules that were in force when it ran.
RULES_VERSION = "fraud-rules/1.0"

#: How wide a neighbourhood to hand back with a finding. One hop shows who is
#: directly attached; the cycle members are added explicitly on top.
GRAPH_RADIUS = 1


@dataclass(frozen=True, slots=True)
class Assessment:
    """One case, judged."""

    assessment_id: str
    case_id: str
    member_id: str
    findings: list[Finding]
    level: str
    integrity: Any
    graph_ref: str
    graph: dict[str, Any]
    snapshot_id: str | None = None
    anomaly_score: float | None = None
    sources: dict[str, str] = field(default_factory=dict)
    latency_ms: float = 0.0

    @property
    def evidence_refs(self) -> list[str]:
        seen: list[str] = []
        for finding in self.findings:
            for ref in finding.evidence_refs:
                if ref not in seen:
                    seen.append(ref)
        return seen

    def as_contract(self) -> dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "case_id": self.case_id,
            "member_id": self.member_id,
            "snapshot_id": self.snapshot_id,
            "findings": [f.as_dict() for f in self.findings],
            "integrity_score": self.integrity.score,
            "level": self.level,
            "calc_id": self.integrity.calc_id,
            "integrity_factor": self.integrity.as_contract(),
            "graph_ref": self.graph_ref,
            "anomaly_score": self.anomaly_score,
            "rules_version": RULES_VERSION,
            "evidence_refs": self.evidence_refs,
            "sources": self.sources,
            "latency_ms": round(self.latency_ms, 2),
        }


def _graph_reference(case_id: str, graph: dict[str, Any]) -> str:
    """Content-addressed, so the same neighbourhood is stored once."""
    return derived_id("ev", "fraud-graph", case_id, sha256(canonical_json(graph)))


def _members_to_show(member_id: str, findings: list[Finding]) -> list[str]:
    """The case's member, plus everyone a finding named.

    A cycle finding is unreadable without the other people in the cycle, so
    they are in the picture even though the case is about one applicant.
    """
    wanted = {member_id}
    for finding in findings:
        wanted.update(finding.members)
    return sorted(wanted)


def judge(bundle: CaseBundle, *, anomaly_score: float | None = None) -> tuple[list[Finding], str, Any]:
    """Rules, level and integrity score for an already-gathered bundle."""
    facts = bundle.facts
    if anomaly_score is not None:
        facts = _with_anomaly(facts, anomaly_score)
    findings = evaluate_rules(facts, bundle.graph)
    level = worst_severity(findings)
    # docs/05 §4: deductions per open finding. Advisory findings inform the
    # reader without pricing the case, so they are named but not deducted.
    integrity = integrity_score(
        open_findings=[{"severity": f.severity, "code": f.code} for f in findings if not f.advisory],
        evidence_refs=tuple(r for f in findings for r in f.evidence_refs),
        derived=True,
    )
    return findings, level, integrity


def _with_anomaly(facts: Any, score: float) -> Any:
    from dataclasses import replace

    return replace(facts, anomaly_score=score)


async def assess(
    source: FactSource,
    *,
    case_id: str,
    member_id: str,
    snapshot_id: str | None = None,
    anomaly_score: float | None = None,
) -> Assessment:
    started = time.perf_counter()
    bundle = await source.gather(case_id=case_id, member_id=member_id, snapshot_id=snapshot_id)
    if anomaly_score is None:
        # The service owns the detector. A caller may still pass a score, which
        # is how a replay reproduces an old assessment against a model version
        # that has since moved on.
        anomaly_score = score_case(bundle)
    findings, level, integrity = judge(bundle, anomaly_score=anomaly_score)

    graph = bundle.graph.subgraph_around(_members_to_show(member_id, findings), radius=GRAPH_RADIUS)
    graph_ref = _graph_reference(case_id, graph)
    elapsed = (time.perf_counter() - started) * 1000.0

    return Assessment(
        assessment_id=derived_id(
            "asmt", RULES_VERSION, case_id, sha256(canonical_json([f.finding_id for f in findings]))
        ),
        case_id=case_id,
        member_id=member_id,
        findings=findings,
        level=level,
        integrity=integrity,
        graph_ref=graph_ref,
        graph=graph,
        snapshot_id=snapshot_id,
        anomaly_score=anomaly_score,
        sources=bundle.sources,
        latency_ms=elapsed,
    )


def empty_graph() -> EntityGraph:
    return EntityGraph()
