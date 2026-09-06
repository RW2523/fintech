"""Scoring the fraud rules against clean cases (docs/00 T-033).

The acceptance is that the planted patterns are found and that ordinary
members are left alone. The second half is the harder one: a cooperative is
full of people who guarantee each other, work for the same employer and apply
in the same week, and a rule set that calls all of that suspicious would bury
the cases that matter.

This runs the real rules over the real population and counts what they say.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

__all__ = ["FraudReport", "measure_fraud"]

ROOT = Path(__file__).resolve().parents[1]

#: docs/00 T-033 — the ceiling for findings raised on clean cases.
FALSE_FINDING_CEILING = 0.03


@dataclass
class FraudReport:
    clean_cases: int = 0
    clean_with_findings: int = 0
    planted_cases: int = 0
    planted_found: int = 0
    by_rule: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    examples: list[dict[str, Any]] = field(default_factory=list)

    @property
    def false_finding_rate(self) -> float:
        return self.clean_with_findings / self.clean_cases if self.clean_cases else 0.0

    @property
    def meets_threshold(self) -> bool:
        return self.false_finding_rate <= FALSE_FINDING_CEILING

    def as_dict(self) -> dict[str, Any]:
        return {
            "clean_cases": self.clean_cases,
            "clean_with_findings": self.clean_with_findings,
            "false_finding_rate": round(self.false_finding_rate, 4),
            "ceiling": FALSE_FINDING_CEILING,
            "planted_cases": self.planted_cases,
            "planted_found": self.planted_found,
            "by_rule": dict(sorted(self.by_rule.items())),
            "examples": self.examples[:10],
            "meets_threshold": self.meets_threshold,
        }


def _rules_module() -> Any:
    """Import the fraud service's own rules, so the measurement is of them."""
    import sys

    service = ROOT / "services" / "fraud"
    if str(service) not in sys.path:
        sys.path.insert(0, str(service))
    import app.graph as graph_module
    import app.rules as rules_module

    return rules_module, graph_module


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def measure_fraud(out: Path = ROOT / "synthetic" / "out", *, limit: int | None = 400) -> FraudReport:
    rules, graphs = _rules_module()

    members = {row["member_id"]: row for row in _read(out / "member.jsonl")}
    accounts_by_id = {row["account_id"]: str(row["member_id"]) for row in _read(out / "account.jsonl")}
    applications = _read(out / "documents" / "applications.jsonl")
    rings = json.loads((out / "rings.json").read_text())["rings"] if (out / "rings.json").is_file() else []
    planted = {m for ring in rings for m in ring["members"]}

    # One graph for the whole population: the rules read a neighbourhood, and
    # building it per case here would only measure the walk, not the rules.
    graph = graphs.EntityGraph()
    for row in _read(out / "guarantor.jsonl"):
        borrower = accounts_by_id.get(row["account_id"])
        if borrower:
            graph.add_guarantee(str(row["guarantor_member_id"]), borrower)

    applied: dict[str, date] = {}
    by_employer_branch: dict[tuple[str, str | None], list[dict[str, Any]]] = defaultdict(list)
    for application in applications:
        member_id = str(application["member_id"])
        when = date.fromisoformat(str(application["created_at"]))
        applied[member_id] = max(applied.get(member_id, when), when)
        member = members.get(member_id, {})
        key = (str(member.get("employer_id")), member.get("branch_id"))
        by_employer_branch[key].append(
            {
                "member_id": member_id,
                "employer_id": member.get("employer_id"),
                "branch_id": member.get("branch_id"),
                "applied_at": when,
            }
        )

    branch_guarantees: dict[str, list[str]] = defaultdict(list)
    for row in _read(out / "guarantor.jsonl"):
        guarantor = members.get(str(row["guarantor_member_id"]), {})
        if guarantor.get("branch_id"):
            branch_guarantees[str(guarantor["branch_id"])].append(str(row["guarantor_member_id"]))

    report = FraudReport()
    considered = applications if limit is None else applications[:limit]

    for application in considered:
        member_id = str(application["member_id"])
        if member_id not in members:
            continue
        member = members[member_id]
        when = date.fromisoformat(str(application["created_at"]))
        key = (str(member.get("employer_id")), member.get("branch_id"))

        facts = rules.CaseFacts(
            case_id=f"case_{application['application_id']}",
            member_id=member_id,
            applied_at=when,
            employer_id=str(member.get("employer_id")),
            branch_id=member.get("branch_id"),
            contact_updated_at=_as_date(member.get("contact_updated_at")),
            recent_applications=tuple(by_employer_branch[key]),
            employer_baseline=_baseline(by_employer_branch[key]),
            applications_by_member=applied,
            branch_guarantees=dict(branch_guarantees),
        )
        found = [f for f in rules.evaluate_rules(facts, graph) if not f.advisory]

        if member_id in planted:
            report.planted_cases += 1
            if any(f.rule == "guarantor_cycle" for f in found):
                report.planted_found += 1
            continue

        report.clean_cases += 1
        if found:
            report.clean_with_findings += 1
            for finding in found:
                report.by_rule[finding.rule] += 1
            if len(report.examples) < 10:
                report.examples.append(
                    {
                        "member_id": member_id,
                        "application_id": application["application_id"],
                        "findings": [
                            {"rule": f.rule, "code": f.code, "severity": f.severity, "detail": f.detail}
                            for f in found
                        ],
                    }
                )
    return report


def _baseline(applications: list[dict[str, Any]]) -> float | None:
    """This employer and branch's usual applications per velocity window."""
    if len(applications) < 2:
        return None
    dates = sorted(a["applied_at"] for a in applications)
    span = max((dates[-1] - dates[0]).days, 1)
    windows = max(span / 7.0, 1.0)
    return len(applications) / windows


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
