"""Ingest, classify and extract, in one place (docs/07 §1.1-§1.3).

The pipeline is deliberately synchronous and pure: bytes in, records out. The
service persists what it returns, which keeps the interesting logic testable
without a database or an object store.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.classify import Classification, classify
from app.extract import ExtractedField, extract_fields
from app.ocr import read_lines
from app.storage import ALLOWED_MIME, MAX_BYTES, MAX_PAGES
from cio_common.errors import ValidationFailed
from cio_common.hashing import sha256
from cio_common.ids import new_id

__all__ = ["ProcessedDocument", "check_upload", "evidence_for_field", "process"]

#: docs/07 §1.3 — a value with no OCR anchor is trusted less.
_UNANCHORED_PENALTY = 0.7


@dataclass(slots=True)
class ProcessedDocument:
    document_id: str
    classification: Classification
    fields: list[ExtractedField]
    pages: int
    sha256: str
    phash: str
    page_png: bytes | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)

    @property
    def min_critical_confidence(self) -> float:
        """The weakest field on the page, which is what DOC-04 gates on."""
        confidences = [f.confidence for f in self.fields if f.value is not None]
        return round(min(confidences), 4) if confidences else 0.0


def check_upload(*, mime: str, size: int) -> None:
    """Refuse anything the pipeline cannot safely handle (docs/07 §1.1)."""
    if mime not in ALLOWED_MIME:
        raise ValidationFailed(f"unsupported MIME type {mime!r}", allowed=sorted(ALLOWED_MIME))
    if size > MAX_BYTES:
        raise ValidationFailed(f"file is {size} bytes, over the {MAX_BYTES} byte limit")
    if size <= 0:
        raise ValidationFailed("file is empty")


def _render_pages(data: bytes, mime: str) -> tuple[list[Any], int]:
    """Page images. A PDF is rasterised at 200 dpi (docs/07 §1.1)."""
    from PIL import Image

    if mime == "application/pdf":
        try:
            from pdf2image import convert_from_bytes

            pages = convert_from_bytes(data, dpi=200)
        except Exception as exc:  # poppler missing, or a malformed file
            raise ValidationFailed(f"could not rasterise the PDF: {exc}") from exc
    else:
        pages = [Image.open(io.BytesIO(data)).convert("RGB")]

    if len(pages) > MAX_PAGES:
        raise ValidationFailed(f"document has {len(pages)} pages, over the {MAX_PAGES} page limit")
    return pages, len(pages)


def _phash(image: Any) -> str:
    import imagehash

    # 256 bits: a 64-bit hash cannot separate two documents that share a layout
    # (measured in T-022).
    return str(imagehash.phash(image, hash_size=16))


def evidence_for_field(document_id: str, extracted: ExtractedField, *, page: int = 1) -> dict[str, Any]:
    """One EvidenceRef per extracted field (docs/03 §2, docs/07 §1.3)."""
    return {
        "schema": "evidence_ref/1.0",
        "evidence_id": new_id("ev"),
        "type": "DOCUMENT_FIELD",
        "source_system": "document-service",
        "source_record_id": document_id,
        "locator": {
            "document_id": document_id,
            "page": page,
            "field_path": extracted.name,
            **({"bbox": list(extracted.bbox)} if extracted.bbox else {}),
        },
        "value": extracted.normalised,
        "display": f"{extracted.name} = {extracted.value}",
        "confidence": extracted.confidence,
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "version": "document-ai/0.1.0",
        "permitted_uses": ["UNDERWRITING", "FRAUD"],
    }


def process(
    data: bytes,
    *,
    mime: str,
    document_id: str | None = None,
    declared_type: str | None = None,
    vision_label: str | None = None,
    vision_confidence: float = 0.0,
) -> ProcessedDocument:
    """Read a document: classify it, extract its fields, mint its evidence."""
    check_upload(mime=mime, size=len(data))
    document_id = document_id or new_id("doc")

    pages, page_count = _render_pages(data, mime)
    first = pages[0]

    lines = read_lines(first)
    classification = classify(lines, vision_label=vision_label, vision_confidence=vision_confidence)

    # The declared type is a hint, never the answer: a member calling a bank
    # statement a payslip must not make it one.
    document_type = classification.required_type
    fields = extract_fields(document_type, lines)

    for extracted in fields:
        if extracted.bbox is None and extracted.value is not None:
            object.__setattr__(extracted, "confidence", round(extracted.confidence * _UNANCHORED_PENALTY, 4))

    buffer = io.BytesIO()
    first.save(buffer, format="PNG")
    page_png = buffer.getvalue()

    return ProcessedDocument(
        document_id=document_id,
        classification=classification,
        fields=fields,
        pages=page_count,
        sha256=sha256(data),
        phash=_phash(first),
        page_png=page_png,
        evidence=[evidence_for_field(document_id, f) for f in fields if f.value is not None],
    )
