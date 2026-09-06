"""Tools over one case (docs/06 §4).

What the officer copilot is allowed to read. Everything here is scoped to the
case in the run: an officer asking about the file in front of them gets the
file in front of them, and a question about another member's case is refused
rather than answered from somewhere else.
"""

from __future__ import annotations

from typing import Any

from ai.tools.client import services
from ai.tools.schemas import ARRAY_OF_OBJECTS, OBJECT, inputs, optional
from cio_tools.registry import tool
from cio_tools.spec import EvidenceSpec, PermittedUse, SideEffect

READ = SideEffect.READ
CASE_WORK = {PermittedUse.UNDERWRITING, PermittedUse.SERVICING}

CASE_ID = {"type": "string", "minLength": 5, "maxLength": 64}


@tool(
    "case.get",
    version="1.0",
    input_schema=inputs(case_id=CASE_ID),
    output_schema=OBJECT,
    purpose_tags=CASE_WORK,
    side_effects=READ,
    backing_service="audit",
)
async def case_get(case_id: str) -> dict[str, Any]:
    """The whole case: its timeline, its documents, and what was decided.

    One call rather than five, because a copilot that has to assemble the case
    from five reads will sometimes assemble half of it and answer from that.
    """
    body = await services().get("audit", f"/reconstruct/{case_id}")
    timeline = body.get("timeline") or []
    documents = _latest_documents(body.get("documents") or [])
    kept = [_timeline_entry(entry) for entry in timeline[-TIMELINE_LIMIT:]]

    return {
        "case_id": case_id,
        "timeline": kept,
        # Said rather than left to be noticed. A copilot answering "the case has
        # three entries" from a truncated list is wrong in a way nobody can see.
        "timeline_omitted": max(0, len(timeline) - len(kept)),
        "documents": documents,
        "findings": body.get("findings") or [],
        "actions": body.get("actions") or [],
        "chain": body.get("chain") or {},
        # Named so an answer can say what it could not read, rather than
        # answering as though the missing part were empty.
        "unavailable": body.get("unavailable") or [],
    }


#: How much of a case's history the copilot is given. A case re-decided ten
#: times carries ten decision records, each with its full body, and the whole
#: reconstruction reached 31,000 tokens on a demo case with four documents:
#: past the model's context window, so every question came back as "the answer
#: was not JSON". The current decision is fetched separately and in full.
TIMELINE_LIMIT = 40

#: What a copilot needs from a superseded ledger entry. The rest is the entry's
#: own body, which is what the reconstruction endpoint is for.
_PAYLOAD_KEYS = (
    "recommendation",
    "route",
    "weighted_score",
    "tier",
    "policy_version",
    "decision_record_id",
    "actor",
    "final_action",
    "override",
    "override_reason_code",
    "document_id",
    "type",
    "status",
    "rule_id",
    "severity",
)


def _timeline_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """One event, said in the words an answer would use.

    The full payload is dropped. A superseded decision record's budgets,
    opinion list and hash tree tell an officer nothing about what happened and
    cost more context than everything else on the case put together.
    """
    payload = entry.get("payload") or {}
    summary = {key: payload[key] for key in _PAYLOAD_KEYS if key in payload}
    kept: dict[str, Any] = {
        key: entry[key] for key in ("source", "kind", "at", "seq", "entry_id") if key in entry
    }
    if summary:
        kept["payload"] = summary
    return kept


def _latest_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per document, the most recent version of each.

    A case seeded more than once carries the same four documents nine times
    over, which reads to a model as thirty-six documents on the file.
    """
    latest: dict[str, dict[str, Any]] = {}
    for document in documents:
        key = str(document.get("document_id") or document.get("type") or id(document))
        current = latest.get(key)
        if current is None or str(document.get("version") or "") >= str(current.get("version") or ""):
            latest[key] = document
    return list(latest.values())


@tool(
    "decision_record.get",
    version="1.0",
    input_schema=inputs(decision_record_id={"type": "string", "minLength": 5}),
    output_schema=OBJECT,
    purpose_tags=CASE_WORK,
    side_effects=READ,
    backing_service="decision",
    evidence=EvidenceSpec(
        type="ANALYTIC_RESULT",
        source_system="decision",
        source_record_path="decision_record_id",
        locator_from={"field_path": "decision_record_id"},
    ),
)
async def decision_record_get(decision_record_id: str) -> dict[str, Any]:
    """What the platform decided, and everything the decision rested on."""
    body = await services().get("decision", f"/decision-records/{decision_record_id}")
    record = dict(body.get("record") or {})
    return {
        "decision_record_id": decision_record_id,
        "case_id": body.get("case_id"),
        "recommendation": record.get("recommendation"),
        "route": record.get("route"),
        "route_reasons": record.get("route_reasons") or [],
        "weighted_score": record.get("weighted_score"),
        "confidence": record.get("confidence"),
        "disagreement": record.get("disagreement"),
        "factor_scores": record.get("factor_scores") or {},
        "hard_gates": record.get("hard_gates") or [],
        "would_change_outcome": record.get("would_change_outcome") or [],
        "required_authority": record.get("required_authority"),
        "policy_version": record.get("policy_version"),
    }


@tool(
    "evidence.search",
    version="1.0",
    input_schema=inputs(
        case_id=CASE_ID,
        query=optional({"type": "string", "maxLength": 200}),
        kind=optional({"type": "string", "maxLength": 40}),
    ),
    output_schema=ARRAY_OF_OBJECTS,
    purpose_tags=CASE_WORK,
    side_effects=READ,
    backing_service="governance",
)
async def evidence_search(
    case_id: str, query: str | None = None, kind: str | None = None
) -> list[dict[str, Any]]:
    """Every piece of evidence on the case, optionally filtered.

    Matching is substring over what the evidence says, not semantic. A copilot
    that half-matched an officer's wording to the wrong payslip would produce a
    citation that looks right and points somewhere else, and a wrong citation
    is worse than no answer.
    """
    body = await services().get("audit", f"/reconstruct/{case_id}")
    found: list[dict[str, Any]] = []

    for event in body.get("timeline") or []:
        payload = event.get("payload") or {}
        for reference in _evidence_in(payload):
            found.append(
                {
                    "evidence_id": reference,
                    "kind": event.get("kind"),
                    "source": event.get("source"),
                    "at": event.get("at"),
                }
            )

    for document in body.get("documents") or []:
        found.append(
            {
                "evidence_id": document.get("document_id"),
                "kind": "DOCUMENT",
                "document_type": document.get("type"),
                "status": document.get("status"),
            }
        )

    if kind:
        found = [item for item in found if str(item.get("kind", "")).upper() == kind.upper()]
    if query:
        needle = query.lower()
        found = [item for item in found if any(needle in str(value).lower() for value in item.values())]
    return found[:50]


def _evidence_in(node: Any) -> list[str]:
    """Every evidence id anywhere in a payload."""
    import re

    pattern = re.compile(r"\bev_[0-9A-HJKMNP-TV-Z]{26}\b")
    return sorted(set(pattern.findall(str(node))))
