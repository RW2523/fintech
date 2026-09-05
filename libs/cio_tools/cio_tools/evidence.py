"""Turn tool output into EvidenceRefs (docs/03 §2, docs/06 §4).

Agents may only cite evidence ids that a tool actually returned during the run,
so the registry mints the refs itself. An agent cannot invent one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from cio_common.ids import new_id
from cio_tools.spec import EvidenceSpec, PermittedUse

__all__ = ["EVIDENCE_KEY", "attach_evidence", "build_evidence", "extract_evidence_ids"]

#: Key under which the registry attaches refs to a tool result.
EVIDENCE_KEY = "evidence_refs"


def _dig(node: Any, path: str | None) -> Any:
    if path is None:
        return None
    current = node
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _locator(record: Any, spec: EvidenceSpec) -> dict[str, Any]:
    locator = {key: _dig(record, path) for key, path in spec.locator_from.items()}
    return {k: v for k, v in locator.items() if v is not None}


def build_evidence(
    payload: Any,
    spec: EvidenceSpec,
    *,
    permitted_uses: frozenset[PermittedUse],
    version: str,
) -> list[dict[str, Any]]:
    """One EvidenceRef per record the tool returned."""
    records = _dig(payload, spec.items_path) if spec.items_path else payload
    if records is None:
        return []
    if not isinstance(records, list):
        records = [records]

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    refs: list[dict[str, Any]] = []

    for record in records:
        locator = _locator(record, spec)
        if not locator:
            # EvidenceRef.locator needs at least one key (docs/03 §2)
            continue
        confidence = _dig(record, spec.confidence_path)
        ref: dict[str, Any] = {
            "schema": "evidence_ref/1.0",
            "evidence_id": new_id("ev"),
            "type": spec.type,
            "source_system": spec.source_system,
            "source_record_id": str(_dig(record, spec.source_record_path) or "-"),
            "locator": locator,
            "confidence": float(confidence)
            if isinstance(confidence, (int, float))
            else spec.default_confidence,
            "captured_at": now,
            "version": version,
            "permitted_uses": sorted(str(u) for u in permitted_uses),
        }
        value = _dig(record, spec.value_path)
        if value is not None:
            ref["value"] = value
        display = _dig(record, spec.display_path)
        if display is not None:
            ref["display"] = str(display)
        refs.append(ref)

    return refs


def attach_evidence(payload: Any, refs: list[dict[str, Any]]) -> Any:
    """Attach refs to the result the agent will see."""
    if not refs:
        return payload
    if isinstance(payload, dict):
        return {**payload, EVIDENCE_KEY: refs}
    return {"result": payload, EVIDENCE_KEY: refs}


def extract_evidence_ids(payload: Any) -> set[str]:
    """Every evidence id anywhere in a tool result."""
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == EVIDENCE_KEY and isinstance(value, list):
                found |= {r["evidence_id"] for r in value if isinstance(r, dict) and "evidence_id" in r}
            elif key in ("evidence_id", "id") and isinstance(value, str) and value.startswith("ev_"):
                found.add(value)
            else:
                found |= extract_evidence_ids(value)
    elif isinstance(payload, list):
        for item in payload:
            found |= extract_evidence_ids(item)
    return found
