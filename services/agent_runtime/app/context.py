"""What the model sees, in the order docs/06 §3 fixes.

The order is not stylistic. The system prompt comes first because it is the
agent's duty. The case summary comes next because everything after it is
evidence about that case. Tool results and clauses follow as they arrive, and
the output instructions come last so the schema is the most recent thing in
the context.

Every string that came from a document, a member or a free-text field arrives
wrapped: the model is told once that `{"data": ...}` is data, and then it
always is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from ai.guardrails.injection import Detection, scan
from ai.guardrails.wrapping import wrap

__all__ = ["Context", "assemble"]

#: docs/06 §3 — the sections, in order. A section with nothing in it is left
#: out rather than sent empty: an empty heading reads as an absence of
#: evidence, which is a different thing from a section that does not apply.
SECTIONS = (
    "CASE_SUMMARY",
    "TOOL_RESULTS",
    "RETRIEVED_CLAUSES",
    "PRIOR_OPINIONS",
    "TEMPORAL_CONTEXT",
    "OUTPUT_INSTRUCTIONS",
)

#: docs/06 §3 — retrieved clause text is capped so a long clause cannot crowd
#: out the case it is supposed to be read against.
CLAUSE_CHARS = 600

OUTPUT_SENTENCE = "Output exactly one JSON object matching the schema. No prose outside JSON."


@dataclass
class Context:
    """One assembled prompt, and what was noticed while assembling it."""

    messages: list[dict[str, str]] = field(default_factory=list)
    injections: list[Detection] = field(default_factory=list)
    evidence_ids: set[str] = field(default_factory=set)
    sections: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sections": self.sections,
            "evidence_ids": sorted(self.evidence_ids),
            "injections": [d.as_dict() for d in self.injections],
        }


def _fenced(label: str, payload: Any) -> str:
    return f"{label}\n```json\n{json.dumps(payload, indent=2, default=str)}\n```"


def _case_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    """A bounded projection of the CaseSnapshot. Never raw documents.

    Free-text fields are wrapped even here, because a purpose or an employer
    name is text somebody typed.
    """
    member = snapshot.get("member") or {}
    return {
        "product_code": snapshot.get("product_code"),
        "amount": snapshot.get("amount"),
        "tenor_months": snapshot.get("tenor_months"),
        "purpose": wrap(snapshot.get("purpose"), "application:purpose") if snapshot.get("purpose") else None,
        "member": {
            "member_ref": member.get("member_ref") or member.get("member_id"),
            "tenure_months": member.get("tenure_months"),
            "branch_id": member.get("branch_id"),
            "employer_sector": member.get("employer_sector"),
        },
        "documents": [
            {
                "document_id": d.get("document_id"),
                "type": d.get("type"),
                "status": d.get("status"),
                "confidence": d.get("confidence"),
            }
            for d in (snapshot.get("documents") or [])
        ],
        "versions": {
            "policy_version": snapshot.get("policy_version"),
            "dff_version": snapshot.get("dff_version"),
            "autonomy_version": snapshot.get("autonomy_version"),
            "model_versions": snapshot.get("model_versions"),
        },
    }


def _clauses(clauses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "clause_id": c.get("clause_id"),
            "version": c.get("version"),
            "text": str(c.get("text") or "")[:CLAUSE_CHARS],
        }
        for c in clauses
    ]


def assemble(
    *,
    prompt: str,
    agent_id: str,
    agent_version: str,
    snapshot: dict[str, Any],
    output_schema: dict[str, Any],
    tool_results: list[dict[str, Any]] | None = None,
    clauses: list[dict[str, Any]] | None = None,
    prior_opinions: list[dict[str, Any]] | None = None,
    temporal_context: dict[str, Any] | None = None,
    today: date | None = None,
) -> Context:
    """Build the conversation for one invocation."""
    context = Context()
    rendered = (
        prompt.replace("{{agent_id}}", agent_id)
        .replace("{{agent_version}}", agent_version)
        .replace("{{policy_version}}", str(snapshot.get("policy_version") or "unknown"))
        .replace("{{today}}", (today or datetime.now(UTC).date()).isoformat())
    )
    context.messages.append({"role": "system", "content": rendered})

    summary = _case_summary(snapshot)
    context.injections.extend(scan(summary, origin="case_summary"))
    context.messages.append({"role": "user", "content": _fenced("CASE_SUMMARY", summary)})
    context.sections.append("CASE_SUMMARY")

    if tool_results:
        context.injections.extend(scan(tool_results, origin="tool_results"))
        for result in tool_results:
            for ref in result.get("evidence_refs") or []:
                context.evidence_ids.add(str(ref.get("evidence_id") if isinstance(ref, dict) else ref))
        context.messages.append({"role": "user", "content": _fenced("TOOL_RESULTS", tool_results)})
        context.sections.append("TOOL_RESULTS")

    if clauses:
        context.messages.append({"role": "user", "content": _fenced("RETRIEVED_CLAUSES", _clauses(clauses))})
        context.sections.append("RETRIEVED_CLAUSES")

    if prior_opinions:
        # docs/06 §3 — structured, and without narratives: an agent revising
        # its view should read what the others found, not how they told it.
        stripped = [{k: v for k, v in opinion.items() if k != "narrative"} for opinion in prior_opinions]
        context.messages.append({"role": "user", "content": _fenced("PRIOR_OPINIONS", stripped)})
        context.sections.append("PRIOR_OPINIONS")

    if temporal_context:
        context.messages.append({"role": "user", "content": _fenced("TEMPORAL_CONTEXT", temporal_context)})
        context.sections.append("TEMPORAL_CONTEXT")

    context.messages.append(
        {
            "role": "user",
            "content": _fenced(
                "OUTPUT_INSTRUCTIONS", {"schema": output_schema, "instruction": OUTPUT_SENTENCE}
            ),
        }
    )
    context.sections.append("OUTPUT_INSTRUCTIONS")
    return context
