"""Persistence for document-service (docs/04 §4)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.extract import ExtractedField
from cio_common.ids import new_id


def decode_json(value: Any) -> Any:
    """Read a jsonb column whatever the driver hands back.

    asyncpg decodes jsonb into Python objects, so a JSON string arrives as a
    plain str and must not be parsed a second time.
    """
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


__all__ = [
    "decode_json",
    "insert_document",
    "insert_extractions",
    "list_findings",
    "load_document",
    "load_extractions",
    "record_finding",
    "save_ground_truth",
    "supersede_field",
]


async def insert_document(
    db: AsyncSession,
    *,
    document_id: str,
    case_id: str | None,
    member_id: str | None,
    object_key: str,
    declared_type: str | None,
) -> None:
    await db.execute(
        text("""
        INSERT INTO app_document.document
          (document_id, case_id, member_id, type, declared_type, object_key, status)
        VALUES (:id, :case_id, :member_id, 'UNKNOWN', :declared, :key, 'UPLOADED')
    """),
        {
            "id": document_id,
            "case_id": case_id,
            "member_id": member_id,
            "declared": declared_type,
            "key": object_key,
        },
    )


async def mark_processed(
    db: AsyncSession,
    *,
    document_id: str,
    document_type: str,
    confidence: float,
    classifier: str,
    needs_human: bool,
    pages: int,
    sha256: str,
    phash: str,
) -> None:
    await db.execute(
        text("""
        UPDATE app_document.document
        SET type = :type, classified_conf = :conf, classifier = :classifier,
            needs_human = :needs_human, pages = :pages, sha256 = :sha256,
            phash = :phash, status = 'PROCESSED', processed_at = now()
        WHERE document_id = :id
    """),
        {
            "id": document_id,
            "type": document_type,
            "conf": confidence,
            "classifier": classifier,
            "needs_human": needs_human,
            "pages": pages,
            "sha256": sha256,
            "phash": phash,
        },
    )


async def insert_extractions(
    db: AsyncSession,
    document_id: str,
    fields: list[ExtractedField],
    evidence: list[dict[str, Any]] | None = None,
) -> None:
    by_field = {
        e["locator"]["field_path"]: e["evidence_id"]
        for e in (evidence or [])
        if "field_path" in e.get("locator", {})
    }
    for extracted in fields:
        await db.execute(
            text("""
            INSERT INTO app_document.extraction
              (extraction_id, document_id, field, value, norm_value, conf, page,
               bbox, method)
            VALUES (:id, :document_id, :field, :value, CAST(:norm AS jsonb), :conf,
                    1, CAST(:bbox AS numeric[]), :method)
        """),
            {
                "id": by_field.get(extracted.name) or new_id("ext"),
                "document_id": document_id,
                "field": extracted.name,
                "value": extracted.value,
                "norm": json.dumps(extracted.normalised),
                "conf": extracted.confidence,
                "bbox": list(extracted.bbox) if extracted.bbox else None,
                "method": extracted.method,
            },
        )


async def load_document(db: AsyncSession, document_id: str) -> dict[str, Any] | None:
    row = (
        (
            await db.execute(
                text("""
        SELECT * FROM app_document.document WHERE document_id = :id
    """),
                {"id": document_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def load_extractions(
    db: AsyncSession, document_id: str, *, current_only: bool = True
) -> list[dict[str, Any]]:
    """Every field read from a document.

    ``current_only`` hides readings a human correction has replaced; the
    originals are kept, never overwritten.
    """
    columns = """
        SELECT extraction_id, field, value, norm_value, conf, page, bbox, method,
               superseded_by
        FROM app_document.extraction
    """
    query = (
        f"{columns} WHERE document_id = :id AND superseded_by IS NULL ORDER BY field"
        if current_only
        else f"{columns} WHERE document_id = :id ORDER BY field"
    )
    rows = (await db.execute(text(query), {"id": document_id})).mappings().all()
    return [dict(r) for r in rows]


async def supersede_field(db: AsyncSession, document_id: str, field: str, replacement: str) -> None:
    await db.execute(
        text("""
        UPDATE app_document.extraction SET superseded_by = :replacement
        WHERE document_id = :id AND field = :field AND superseded_by IS NULL
          AND extraction_id <> :replacement
    """),
        {"id": document_id, "field": field, "replacement": replacement},
    )


async def record_finding(
    db: AsyncSession,
    *,
    case_id: str | None,
    document_id: str | None,
    code: str,
    severity: str,
    detail: dict[str, Any],
    evidence_refs: list[str] | None = None,
) -> str:
    finding_id = new_id("fnd")
    await db.execute(
        text("""
        INSERT INTO app_document.finding
          (finding_id, case_id, document_id, code, severity, detail, evidence_refs)
        VALUES (:id, :case_id, :document_id, :code, :severity,
                CAST(:detail AS jsonb), CAST(:refs AS jsonb))
    """),
        {
            "id": finding_id,
            "case_id": case_id,
            "document_id": document_id,
            "code": code,
            "severity": severity,
            "detail": json.dumps(detail),
            "refs": json.dumps(evidence_refs or []),
        },
    )
    return finding_id


async def list_findings(
    db: AsyncSession, *, case_id: str | None = None, document_id: str | None = None
) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                text("""
        SELECT * FROM app_document.finding
        WHERE (CAST(:case_id AS text) IS NULL OR case_id = :case_id)
          AND (CAST(:document_id AS text) IS NULL OR document_id = :document_id)
        ORDER BY created_at
    """),
                {"case_id": case_id, "document_id": document_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def save_ground_truth(db: AsyncSession, document_id: str, body: dict[str, Any]) -> None:
    """Synthetic only. Never read by the extraction path."""
    await db.execute(
        text("""
        INSERT INTO app_document.ground_truth (document_id, body)
        VALUES (:id, CAST(:body AS jsonb))
        ON CONFLICT (document_id) DO UPDATE SET body = EXCLUDED.body
    """),
        {"id": document_id, "body": json.dumps(body)},
    )
