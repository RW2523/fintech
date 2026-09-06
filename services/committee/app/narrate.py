"""Turning the decision record into three narratives (docs/06 §8).

The structure decides; the narrative describes. Three audiences read the same
decision differently: a member needs to know what happens next, an officer
needs the reasoning, an auditor needs the trail. All three come from the
record and from nothing else.

If the model cannot be reached, the narrative is written from a template and
marked DEGRADED. A missing narrative would be better than a wrong one, but a
plain one is better than either.
"""

from __future__ import annotations

from typing import Any

__all__ = ["NARRATIVE_SCHEMA", "degraded_narrative", "narrative_prompt"]

#: docs/03 — the shape a DecisionRecord's narrative field takes.
NARRATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["member", "officer", "auditor"],
    "properties": {
        "member": {"type": "string", "maxLength": 700},
        "officer": {"type": "string", "maxLength": 1200},
        "auditor": {"type": "string", "maxLength": 1500},
    },
}

SYSTEM = """\
You write three short accounts of one credit decision that has already been \
made. You are not deciding anything and you may not change or soften what the \
record says.

Write from the DECISION_RECORD below and from nothing else. Every figure you \
give must appear in it. Do not add a figure, a reason or a condition that is \
not there, and do not name a member.

- member: plain, warm, second person. What was decided, the main reason in \
everyday words, and what happens next. No jargon, no reason codes, no \
probabilities.
- officer: the reasoning. Which factor decided it, what the gates said, what \
is unresolved, and what would have changed the outcome.
- auditor: the trail. Versions, the decisive factor and its calculation, the \
route and why, and any override.

Never say a decision is guaranteed, never predict an outcome, and never \
mention a protected characteristic.
"""


def narrative_prompt(record: dict[str, Any]) -> list[dict[str, str]]:
    """The conversation the narrator is given."""
    import json

    #: Only the fields a narrative may draw on. Sending the whole record would
    #: let the narrator quote things no audience should see.
    readable = {
        key: record.get(key)
        for key in (
            "recommendation",
            "route",
            "route_reasons",
            "tier",
            "weighted_score",
            "confidence",
            "disagreement",
            "challenger_open",
            "required_authority",
            "hard_gates",
            "factor_scores",
            "would_change_outcome",
            "reason_codes",
            "policy_version",
            "dff_version",
            "model_versions",
            "evidence_coverage",
        )
        if record.get(key) is not None
    }
    return [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": "DECISION_RECORD\n```json\n" + json.dumps(readable, indent=2, default=str) + "\n```",
        },
    ]


def _decisive(record: dict[str, Any]) -> str:
    for family, entry in (record.get("factor_scores") or {}).items():
        if entry.get("decisive"):
            return str(family)
    return "the weighted assessment"


def degraded_narrative(record: dict[str, Any]) -> dict[str, Any]:
    """A deterministic narrative for when the model cannot be reached.

    Written from the record's own fields, so it says less than a generated one
    and nothing that is not true.
    """
    recommendation = str(record.get("recommendation") or "REVIEW")
    route = str(record.get("route") or "OFFICER_REVIEW")
    decisive = _decisive(record)
    blockers = [
        g.get("rule_id")
        for g in (record.get("hard_gates") or [])
        if str(g.get("result", "")).upper() in {"FAIL", "BLOCK"}
    ]

    member = (
        "Your application has been assessed and is with our team for a "
        "decision. We will contact you when there is news."
    )
    if recommendation == "APPROVE":
        member = (
            "Your application has been assessed and the recommendation "
            "is to approve it. A member of our team will confirm the "
            "next steps with you."
        )
    elif blockers:
        member = (
            "Your application has been assessed and we need to look at "
            "it more closely before deciding. A member of our team will "
            "be in touch."
        )

    officer = f"Recommendation {recommendation}, routed to {route}. The decisive factor was {decisive}." + (
        f" Failing gates: {', '.join(str(b) for b in blockers)}." if blockers else " No gate failed."
    )

    auditor = (
        f"Decision under policy {record.get('policy_version', 'unknown')} "
        f"and framework {record.get('dff_version', 'unknown')}; tier "
        f"{record.get('tier', 'unknown')}; route {route} for "
        f"{', '.join(record.get('route_reasons') or []) or 'no recorded reason'}. "
        "Narrative generated from the record without the language model, "
        "which was unavailable."
    )

    return {"member": member, "officer": officer, "auditor": auditor, "status": "DEGRADED"}
