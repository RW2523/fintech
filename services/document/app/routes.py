"""document-service endpoints (docs/08 §2)."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response, UploadFile
from sqlalchemy import text

from app import repository
from app.classify import CLASSIFICATION_FLOOR
from app.db import session
from app.extract import ExtractedField, normalise
from app.models import CompleteRequest, ReviewRequest, UploadRequest
from app.pipeline import check_upload, evidence_for_field, process
from app.storage import DOCUMENTS_BUCKET, PAGES_BUCKET, storage
from cio_common.errors import NotFound, ValidationFailed
from cio_common.ids import new_id

router = APIRouter(tags=["document"])


def _object_key(case_id: str, document_id: str, filename: str) -> str:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"{case_id}/{document_id}.{suffix}"


@router.post("/cases/{case_id}/documents", summary="Ask for somewhere to upload")
async def request_upload(case_id: str, body: UploadRequest) -> dict[str, Any]:
    """Returns a presigned URL; the bytes never pass through this service."""
    check_upload(mime=body.mime, size=body.size)

    document_id = new_id("doc")
    key = _object_key(case_id, document_id, body.filename)

    async with session() as db:
        await repository.insert_document(
            db,
            document_id=document_id,
            case_id=case_id,
            member_id=body.member_id,
            object_key=key,
            declared_type=body.declared_type,
        )

    # MinIO absent: the caller can still POST the bytes to the fallback below.
    upload_url: str | None = None
    with contextlib.suppress(Exception):
        store = storage()
        store.ensure_buckets()
        upload_url = store.presigned_put(DOCUMENTS_BUCKET, key)

    return {
        "document_id": document_id,
        "object_key": key,
        "upload_url": upload_url,
        "fallback_upload": f"/documents/{document_id}/upload",
    }


@router.post("/documents/{document_id}/upload", summary="Upload bytes directly")
async def upload_bytes(document_id: str, file: UploadFile) -> dict[str, Any]:
    """Used when a presigned URL is not available, and by the seed loader."""
    data = await file.read()
    check_upload(mime=file.content_type or "application/octet-stream", size=len(data))

    async with session() as db:
        document = await repository.load_document(db, document_id)
    if document is None:
        raise NotFound(f"no document {document_id!r}")

    storage().ensure_buckets()
    storage().put(
        DOCUMENTS_BUCKET,
        document["object_key"],
        data,
        content_type=file.content_type or "application/octet-stream",
    )
    return {"document_id": document_id, "bytes": len(data)}


@router.post("/documents/{document_id}/complete", summary="Classify and extract")
async def complete(document_id: str, body: CompleteRequest) -> dict[str, Any]:
    """docs/07 §1.1-§1.3 — render, classify, extract, mint evidence."""
    async with session() as db:
        document = await repository.load_document(db, document_id)
        if document is None:
            raise NotFound(f"no document {document_id!r}")

        try:
            data = storage().get(DOCUMENTS_BUCKET, document["object_key"])
        except Exception as exc:
            raise NotFound(f"no stored object for {document_id!r}: {exc}") from exc

        mime = "application/pdf" if document["object_key"].endswith(".pdf") else "image/png"
        processed = process(data, mime=mime, document_id=document_id, declared_type=document["declared_type"])

        if body.sha256 and body.sha256 != processed.sha256:
            raise ValidationFailed(
                "uploaded bytes do not match the declared digest",
                declared=body.sha256,
                actual=processed.sha256,
            )

        await repository.mark_processed(
            db,
            document_id=document_id,
            document_type=processed.classification.required_type,
            confidence=processed.classification.confidence,
            classifier=processed.classification.method,
            needs_human=processed.classification.needs_human,
            pages=processed.pages,
            sha256=processed.sha256,
            phash=processed.phash,
        )
        await repository.insert_extractions(db, document_id, processed.fields, processed.evidence)

        if processed.classification.needs_human:
            await repository.record_finding(
                db,
                case_id=document["case_id"],
                document_id=document_id,
                code="DOC-02",
                severity="MEDIUM",
                detail={
                    "reason": "classification below the confidence floor",
                    "confidence": processed.classification.confidence,
                    "floor": CLASSIFICATION_FLOOR,
                },
            )

    if processed.page_png:
        # The page cache speeds up the evidence viewer; a document is fully
        # processed without it, so a failure here must not fail the request.
        with contextlib.suppress(Exception):
            storage().put(PAGES_BUCKET, f"{document_id}/1.png", processed.page_png, content_type="image/png")

    return {
        "document_id": document_id,
        "type": processed.classification.required_type,
        "label": processed.classification.label,
        "classified_conf": processed.classification.confidence,
        "classifier": processed.classification.method,
        "needs_human": processed.classification.needs_human,
        "pages": processed.pages,
        "fields_extracted": sum(1 for f in processed.fields if f.value is not None),
        "min_field_confidence": processed.min_critical_confidence,
        "sha256": processed.sha256,
        "phash": processed.phash,
    }


@router.get("/documents/{document_id}", summary="Document metadata")
async def get_document(document_id: str) -> dict[str, Any]:
    async with session() as db:
        document = await repository.load_document(db, document_id)
    if document is None:
        raise NotFound(f"no document {document_id!r}")
    return {
        "document_id": document["document_id"],
        "case_id": document["case_id"],
        "member_id": document["member_id"],
        "type": document["type"],
        "declared_type": document["declared_type"],
        "status": document["status"],
        "pages": document["pages"],
        "classified_conf": float(document["classified_conf"] or 0),
        "classifier": document["classifier"],
        "needs_human": document["needs_human"],
        "sha256": document["sha256"],
        "phash": document["phash"],
    }


@router.get("/documents/{document_id}/extraction", summary="Extracted fields with boxes and confidence")
async def get_extraction(document_id: str) -> dict[str, Any]:
    async with session() as db:
        document = await repository.load_document(db, document_id)
        if document is None:
            raise NotFound(f"no document {document_id!r}")
        rows = await repository.load_extractions(db, document_id)

    return {
        "document_id": document_id,
        "type": document["type"],
        "fields": [
            {
                "field": row["field"],
                "value": row["value"],
                "norm_value": repository.decode_json(row["norm_value"]),
                "conf": float(row["conf"]),
                "page": row["page"],
                "bbox": [float(v) for v in row["bbox"]] if row["bbox"] else None,
                "method": row["method"],
                "evidence_id": row["extraction_id"] if row["extraction_id"].startswith("ev_") else None,
            }
            for row in rows
        ],
    }


@router.get("/documents/{document_id}/page/{page}.png", summary="A rendered page")
async def get_page(document_id: str, page: int) -> Response:
    try:
        data = storage().get(PAGES_BUCKET, f"{document_id}/{page}.png")
    except Exception as exc:
        raise NotFound(f"no rendered page {page} for {document_id!r}") from exc
    return Response(content=data, media_type="image/png")


@router.post("/documents/{document_id}/review", summary="A human corrects an extracted field")
async def review(document_id: str, body: ReviewRequest, request: Request) -> dict[str, Any]:
    """A correction becomes HUMAN_INPUT evidence; the machine reading is kept."""
    async with session() as db:
        document = await repository.load_document(db, document_id)
        if document is None:
            raise NotFound(f"no document {document_id!r}")

        current = {row["field"] for row in await repository.load_extractions(db, document_id)}
        if body.field not in current:
            raise ValidationFailed(f"{document['type']} has no field {body.field!r}", fields=sorted(current))

        corrected = ExtractedField(
            name=body.field,
            value=body.value,
            normalised=normalise(body.field, body.value),
            confidence=1.0,
            bbox=None,
            method="human",
        )
        evidence = evidence_for_field(document_id, corrected)
        evidence["type"] = "HUMAN_INPUT"
        evidence["source_system"] = "document-service/review"
        evidence["display"] = f"{body.field} corrected by {body.actor_id}"

        await repository.supersede_field(db, document_id, body.field, evidence["evidence_id"])
        await repository.insert_extractions(db, document_id, [corrected], [evidence])

    return {
        "document_id": document_id,
        "field": body.field,
        "value": body.value,
        "evidence_id": evidence["evidence_id"],
        "note": body.note,
        "reviewed_by": body.actor_id,
        "reviewed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "trace_id": request.headers.get("X-Trace-Id"),
    }


@router.get("/cases/{case_id}/documents", summary="Every document on a case")
async def list_case_documents(case_id: str) -> dict[str, Any]:
    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT document_id, type, version, status, classified_conf, pages,
                   needs_human
            FROM app_document.document WHERE case_id = :case_id
            ORDER BY uploaded_at
        """),
                    {"case_id": case_id},
                )
            )
            .mappings()
            .all()
        )
    return {
        "case_id": case_id,
        "documents": [{**dict(r), "classified_conf": float(r["classified_conf"] or 0)} for r in rows],
    }


@router.get("/cases/{case_id}/findings", summary="Findings raised on a case")
async def case_findings(case_id: str) -> dict[str, Any]:
    async with session() as db:
        findings = await repository.list_findings(db, case_id=case_id)
    return {
        "case_id": case_id,
        "findings": [
            {
                "finding_id": f["finding_id"],
                "document_id": f["document_id"],
                "code": f["code"],
                "severity": f["severity"],
                "status": f["status"],
                "detail": repository.decode_json(f["detail"]),
            }
            for f in findings
        ],
    }
