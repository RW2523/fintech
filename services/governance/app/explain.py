"""Assembling the structured explanation (docs/07 §6).

Five levels, in the order a person reads them: what the policy said, how the
factors scored, what the models drove on, where the evidence came from, and
what a human decided. The structure is authoritative. A narrator turns it into
sentences later, and it may only use what is here.

The rule that matters most is the last one enforced: every identifier the
explanation carries has to appear in the records it was built from. A
narrative that cites an evidence id nobody produced is worse than no
narrative, because it reads as proof.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.sources import DecisionBundle

__all__ = ["Explanation", "InventedIdentifierError", "build_explanation", "collect_identifiers"]

#: Identifiers the platform mints (CLAUDE.md §7). Anything shaped like one has
#: to be traceable to the run, or it is an invention.
_ID = re.compile(
    r"\b((?:snap|ev|op|dr|hd|act|tok|run|case|app|doc|calc|mr|fs|tc|"
    r"alert|msg|sbx|smp|ent|aud|inv|evt|fc|asmt|ext|fnd)_"
    r"[0-9A-HJKMNP-TV-Z]{26})\b"
)

#: A factor is decisive when the record says so; the flag is computed by the
#: synthesizer, not re-derived here, because two derivations can disagree.
_FACTOR_ORDER = ("CAPACITY", "CONDUCT", "COMMITMENT", "CONDITIONS", "INTEGRITY")


class InventedIdentifierError(ValueError):
    """The explanation names something the run never produced."""

    def __init__(self, unknown: set[str]) -> None:
        super().__init__("explanation cites identifiers absent from the run: " + ", ".join(sorted(unknown)))
        self.unknown = unknown


@dataclass
class Explanation:
    """The five levels docs/07 §6 requires, plus what is missing and why."""

    decision_record_id: str
    policy: list[dict[str, Any]] = field(default_factory=list)
    factors: list[dict[str, Any]] = field(default_factory=list)
    drivers: list[dict[str, Any]] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    human: dict[str, Any] | None = None
    counterfactuals: list[dict[str, Any]] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_record_id": self.decision_record_id,
            "levels": {
                "policy": self.policy,
                "factors": self.factors,
                "drivers": self.drivers,
                "provenance": self.provenance,
                "human": self.human,
            },
            "counterfactuals": self.counterfactuals,
            "unavailable": self.unavailable,
            "sources": self.sources,
        }


def collect_identifiers(node: Any) -> set[str]:
    """Every platform identifier anywhere in a record."""
    found: set[str] = set()
    if isinstance(node, str):
        found.update(_ID.findall(node))
    elif isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                found.update(_ID.findall(key))
            found |= collect_identifiers(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            found |= collect_identifiers(item)
    return found


def _policy_level(decision: dict[str, Any]) -> list[dict[str, Any]]:
    """What the rules said, with the clause each one came from."""
    out: list[dict[str, Any]] = []
    for gate in decision.get("hard_gates") or []:
        out.append(
            {
                "rule_id": gate.get("rule_id"),
                "result": gate.get("result"),
                "reason_code": gate.get("reason_code"),
                "clause_id": gate.get("clause_id") or gate.get("rule_id"),
                "evidence_refs": list(gate.get("evidence_refs") or []),
                "blocking": str(gate.get("result", "")).upper() in {"FAIL", "BLOCK"},
            }
        )
    return out


def _factor_level(decision: dict[str, Any]) -> list[dict[str, Any]]:
    """The factor table, in the framework's own order."""
    scores = decision.get("factor_scores") or {}
    out: list[dict[str, Any]] = []
    for family in _FACTOR_ORDER:
        entry = scores.get(family)
        if not entry:
            continue
        out.append(
            {
                "family": family,
                "score": entry.get("score"),
                "weight": entry.get("weight"),
                "weighted": entry.get("weighted"),
                "decisive": bool(entry.get("decisive")),
                "calc_id": entry.get("calc_id"),
                "evidence_refs": list(entry.get("evidence_refs") or []),
                "level": entry.get("level"),
            }
        )
    return out


def _driver_level(bundle: DecisionBundle) -> list[dict[str, Any]]:
    """What moved the model, with the approved wording for each driver."""
    run = bundle.model_run
    if not run:
        return []
    out: list[dict[str, Any]] = []
    for driver in run.get("drivers") or []:
        out.append(
            {
                "feature": driver.get("feature"),
                "direction": driver.get("direction"),
                "contribution": driver.get("contribution"),
                "share": driver.get("share"),
                "reason_code": driver.get("reason_code"),
                "model_run_id": run.get("model_run_id"),
            }
        )
    return out


def _provenance_level(bundle: DecisionBundle) -> list[dict[str, Any]]:
    """Where each piece of evidence came from."""
    out: list[dict[str, Any]] = []
    for ref in (bundle.model_run or {}).get("evidence_refs") or []:
        if isinstance(ref, dict):
            out.append(
                {
                    "evidence_id": ref.get("evidence_id"),
                    "type": ref.get("type"),
                    "source_system": ref.get("source_system"),
                    "source_record_id": ref.get("source_record_id"),
                    "locator": ref.get("locator"),
                    "captured_at": ref.get("captured_at"),
                }
            )
    for document in (bundle.documents or {}).get("documents") or []:
        out.append(
            {
                "document_id": document.get("document_id"),
                "type": document.get("type"),
                "status": document.get("status"),
                "source_system": "document",
            }
        )
    for finding in (bundle.fraud or {}).get("findings") or []:
        out.append(
            {
                "finding_id": finding.get("finding_id"),
                "code": finding.get("code"),
                "severity": finding.get("severity"),
                "advisory": finding.get("advisory"),
                "source_system": "fraud",
            }
        )
    return out


def _human_level(bundle: DecisionBundle) -> dict[str, Any] | None:
    """What a person decided, and why they departed from the recommendation."""
    human = bundle.human_decision
    if not human:
        return None
    return {
        "human_decision_id": human.get("human_decision_id"),
        "decided_by": human.get("decided_by_role") or human.get("role"),
        "final_action": human.get("final_action"),
        "override": bool(human.get("override")),
        "override_reason_code": human.get("override_reason_code"),
        "override_note": human.get("override_note"),
        "decided_at": human.get("decided_at"),
    }


def build_explanation(bundle: DecisionBundle) -> Explanation:
    """The structured explanation, checked against what the run produced."""
    decision = bundle.decision
    explanation = Explanation(
        decision_record_id=str(decision.get("decision_record_id", "")),
        policy=_policy_level(decision),
        factors=_factor_level(decision),
        drivers=_driver_level(bundle),
        provenance=_provenance_level(bundle),
        human=_human_level(bundle),
        counterfactuals=list(decision.get("would_change_outcome") or []),
        sources=dict(bundle.sources),
    )
    explanation.unavailable = sorted(name for name, state in bundle.sources.items() if state == "unavailable")

    known = (
        collect_identifiers(decision)
        | collect_identifiers(bundle.model_run)
        | collect_identifiers(bundle.fraud)
        | collect_identifiers(bundle.documents)
        | collect_identifiers(bundle.human_decision)
    )
    cited = collect_identifiers(explanation.as_dict())
    unknown = cited - known
    if unknown:
        # Refuse rather than trim: an explanation that cites something the run
        # never produced is a defect in whatever assembled it, and quietly
        # dropping the id would hide that.
        raise InventedIdentifierError(unknown)
    return explanation
