"""Tools over the document service (docs/06 §4).

Four reads about one case file: what is in it, what was extracted, what
forensics found, and whether the sources agree. Nothing here judges: a finding
is an observation, and the agent that reads it decides what it means.
"""

from __future__ import annotations

from typing import Any

from ai.tools.client import services
from ai.tools.schemas import (
    ARRAY_OF_OBJECTS,
    CASE_ID,
    DOCUMENT_ID,
    inputs,
    optional,
)
from cio_tools.registry import tool
from cio_tools.spec import EvidenceSpec, PermittedUse, SideEffect

READ = SideEffect.READ
UNDERWRITING = {PermittedUse.UNDERWRITING}
UNDERWRITING_AND_FRAUD = {PermittedUse.UNDERWRITING, PermittedUse.FRAUD}


@tool(
    "documents.list",
    version="1.0",
    input_schema=inputs(case_id=CASE_ID),
    output_schema=ARRAY_OF_OBJECTS,
    purpose_tags=UNDERWRITING_AND_FRAUD,
    side_effects=READ,
    backing_service="document",
    evidence=EvidenceSpec(
        type="DOCUMENT_FIELD",
        source_system="document",
        source_record_path="document_id",
        locator_from={"document_id": "document_id"},
    ),
)
async def documents_list(case_id: str) -> list[dict[str, Any]]:
    """Every document on a case, with its type, status and classification."""
    body = await services().get("document", f"/cases/{case_id}/documents")
    return [
        {
            "document_id": d.get("document_id"),
            "type": d.get("type"),
            "version": d.get("version"),
            "status": d.get("status"),
            "classified_conf": d.get("classified_conf") or d.get("confidence"),
            "pages": d.get("pages"),
        }
        for d in (body.get("documents") if isinstance(body, dict) else body) or []
    ]


@tool(
    "extraction.get",
    version="1.0",
    input_schema=inputs(
        document_id=DOCUMENT_ID, fields=optional({"type": "array", "items": {"type": "string"}})
    ),
    output_schema=ARRAY_OF_OBJECTS,
    purpose_tags=UNDERWRITING_AND_FRAUD,
    side_effects=READ,
    backing_service="document",
    evidence=EvidenceSpec(
        type="DOCUMENT_FIELD",
        source_system="document",
        source_record_path="document_id",
        locator_from={"document_id": "document_id", "field_path": "field", "page": "page", "bbox": "bbox"},
        value_path="value",
        confidence_path="conf",
    ),
)
async def extraction_get(document_id: str, fields: list[str] | None = None) -> list[dict[str, Any]]:
    """Extracted fields with their confidence and where they sit on the page."""
    body = await services().get("document", f"/documents/{document_id}/extraction")
    wanted = set(fields or [])
    out: list[dict[str, Any]] = []
    for entry in (body.get("fields") if isinstance(body, dict) else body) or []:
        name = entry.get("name") or entry.get("field")
        if wanted and name not in wanted:
            continue
        out.append(
            {
                "document_id": document_id,
                "field": name,
                "value": entry.get("value"),
                "norm_value": entry.get("norm_value"),
                "conf": entry.get("conf") or entry.get("confidence"),
                "page": entry.get("page"),
                "bbox": entry.get("bbox"),
            }
        )
    return out


@tool(
    "forensics.get",
    version="1.0",
    input_schema=inputs(document_id=DOCUMENT_ID),
    output_schema=ARRAY_OF_OBJECTS,
    purpose_tags=UNDERWRITING_AND_FRAUD,
    side_effects=READ,
    backing_service="document",
    evidence=EvidenceSpec(
        type="ANALYTIC_RESULT",
        source_system="document",
        source_record_path="document_id",
        locator_from={"document_id": "document_id"},
    ),
)
async def forensics_get(document_id: str) -> list[dict[str, Any]]:
    """What the forensics pass observed about one document.

    An observation, never a conclusion: a page-hash match is a lead until
    content confirms it (T-022 measured why).
    """
    body = await services().get("document", f"/documents/{document_id}")
    findings = (body or {}).get("findings") or []
    return [
        {
            "document_id": document_id,
            "code": f.get("code"),
            "severity": f.get("severity"),
            "detail": f.get("detail"),
        }
        for f in findings
    ]


@tool(
    "reconciliation.get",
    version="1.0",
    input_schema=inputs(case_id=CASE_ID),
    output_schema=ARRAY_OF_OBJECTS,
    purpose_tags=UNDERWRITING_AND_FRAUD,
    side_effects=READ,
    backing_service="document",
    evidence=EvidenceSpec(
        type="ANALYTIC_RESULT",
        source_system="document",
        source_record_path="finding_id",
        locator_from={"document_id": "document_id"},
    ),
)
async def reconciliation_get(case_id: str) -> list[dict[str, Any]]:
    """Where the case file disagrees with itself or with the member record."""
    body = await services().get("document", f"/cases/{case_id}/findings")
    return [
        {
            "finding_id": f.get("finding_id"),
            "code": f.get("code"),
            "severity": f.get("severity"),
            "document_id": f.get("document_id"),
            "detail": f.get("detail"),
            "sources": (f.get("detail") or {}).get("sources"),
            "variance": (f.get("detail") or {}).get("variance"),
        }
        for f in (body or {}).get("findings") or []
    ]
