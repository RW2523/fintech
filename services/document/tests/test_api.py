"""T-023 — the document-service HTTP surface (docs/08 §2)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.ocr import ocr_available
from tests.conftest import minio_is_up

pytestmark = pytest.mark.skipif(
    not ocr_available() or not minio_is_up(),
    reason="needs Tesseract and MinIO; run `make up`",
)

CASE = "case_01JQZK7M8N9P0Q1R2S3T4V5W6X"


async def upload(
    client: AsyncClient, path: Path, *, mime: str = "image/png", declared: str | None = None
) -> str:
    data = await asyncio.to_thread(path.read_bytes)
    response = await client.post(
        f"/cases/{CASE}/documents",
        json={
            "filename": path.name,
            "mime": mime,
            "size": len(data),
            "member_id": "M-000042",
            "declared_type": declared,
        },
    )
    assert response.status_code == 200, response.text
    document_id = response.json()["document_id"]

    put = await client.post(f"/documents/{document_id}/upload", files={"file": (path.name, data, mime)})
    assert put.status_code == 200, put.text
    return str(document_id)


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------
async def test_requesting_an_upload_returns_a_document_id(
    client: AsyncClient, sample_documents: dict[str, Path]
) -> None:
    path = sample_documents["IDENTITY"]
    response = await client.post(
        f"/cases/{CASE}/documents",
        json={"filename": path.name, "mime": "image/png", "size": path.stat().st_size},
    )
    body = response.json()
    assert body["document_id"].startswith("doc_")
    assert body["object_key"].startswith(f"{CASE}/")


async def test_an_unsupported_type_is_refused_before_any_bytes_move(client: AsyncClient) -> None:
    response = await client.post(
        f"/cases/{CASE}/documents", json={"filename": "payload.zip", "mime": "application/zip", "size": 100}
    )
    assert response.status_code == 422
    assert "unsupported MIME" in response.json()["error"]["message"]


async def test_an_oversized_upload_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        f"/cases/{CASE}/documents",
        json={"filename": "big.png", "mime": "image/png", "size": 30 * 1024 * 1024},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# processing
# ---------------------------------------------------------------------------
async def test_processing_classifies_and_extracts(
    client: AsyncClient, sample_documents: dict[str, Path]
) -> None:
    document_id = await upload(client, sample_documents["PAYSLIP_LATEST_3"])
    body = (await client.post(f"/documents/{document_id}/complete", json={})).json()

    assert body["type"] == "PAYSLIP_LATEST_3"
    assert body["label"] == "PAYSLIP"
    assert body["classified_conf"] >= 0.85
    assert body["needs_human"] is False
    assert body["fields_extracted"] >= 8
    assert len(body["sha256"]) == 64
    assert body["phash"]


async def test_the_declared_type_does_not_decide_the_actual_type(
    client: AsyncClient, sample_documents: dict[str, Path]
) -> None:
    """A member calling a bank statement a payslip must not make it one."""
    document_id = await upload(client, sample_documents["BANK_STATEMENT_3M"], declared="PAYSLIP_LATEST_3")
    body = (await client.post(f"/documents/{document_id}/complete", json={})).json()
    assert body["type"] == "BANK_STATEMENT_3M"


async def test_a_digest_mismatch_is_refused(client: AsyncClient, sample_documents: dict[str, Path]) -> None:
    document_id = await upload(client, sample_documents["IDENTITY"])
    response = await client.post(f"/documents/{document_id}/complete", json={"sha256": "0" * 64})
    assert response.status_code == 422
    assert "digest" in response.json()["error"]["message"]


async def test_processing_an_unknown_document_is_not_found(client: AsyncClient) -> None:
    response = await client.post("/documents/doc_01JQZK7M8N9P0Q1R2S3T4V5W6Z/complete", json={})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# extraction results
# ---------------------------------------------------------------------------
async def test_extraction_returns_fields_with_boxes_and_confidence(
    client: AsyncClient, sample_documents: dict[str, Path]
) -> None:
    document_id = await upload(client, sample_documents["PAYSLIP_LATEST_3"])
    await client.post(f"/documents/{document_id}/complete", json={})
    body = (await client.get(f"/documents/{document_id}/extraction")).json()

    assert body["type"] == "PAYSLIP_LATEST_3"
    read = [f for f in body["fields"] if f["value"] is not None]
    assert read
    for row in read:
        assert row["bbox"] and len(row["bbox"]) == 4
        assert 0.0 < row["conf"] <= 1.0
        assert row["method"] == "ocr"


async def test_a_rendered_page_is_available(client: AsyncClient, sample_documents: dict[str, Path]) -> None:
    document_id = await upload(client, sample_documents["IDENTITY"])
    await client.post(f"/documents/{document_id}/complete", json={})
    response = await client.get(f"/documents/{document_id}/page/1.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


async def test_case_documents_are_listed(client: AsyncClient, sample_documents: dict[str, Path]) -> None:
    for path in list(sample_documents.values())[:2]:
        document_id = await upload(client, path)
        await client.post(f"/documents/{document_id}/complete", json={})

    body = (await client.get(f"/cases/{CASE}/documents")).json()
    assert len(body["documents"]) == 2
    assert all(d["status"] == "PROCESSED" for d in body["documents"])


# ---------------------------------------------------------------------------
# human review
# ---------------------------------------------------------------------------
async def test_a_correction_supersedes_the_machine_reading(
    client: AsyncClient, sample_documents: dict[str, Path]
) -> None:
    """docs/08 §2 — a correction becomes evidence in its own right."""
    document_id = await upload(client, sample_documents["PAYSLIP_LATEST_3"])
    await client.post(f"/documents/{document_id}/complete", json={})

    before = (await client.get(f"/documents/{document_id}/extraction")).json()
    original = next(f for f in before["fields"] if f["field"] == "net_salary")

    response = await client.post(
        f"/documents/{document_id}/review",
        json={
            "field": "net_salary",
            "value": "9,999.99",
            "note": "confirmed with the employer",
            "actor_id": "u-officer-1",
        },
    )
    assert response.status_code == 200
    assert response.json()["evidence_id"].startswith("ev_")

    after = (await client.get(f"/documents/{document_id}/extraction")).json()
    corrected = next(f for f in after["fields"] if f["field"] == "net_salary")
    assert corrected["value"] == "9,999.99"
    assert corrected["method"] == "human"
    assert corrected["conf"] == 1.0
    assert corrected["value"] != original["value"]


async def test_the_machine_reading_is_kept_not_overwritten(
    client: AsyncClient, sample_documents: dict[str, Path]
) -> None:
    from app.db import session
    from app.repository import load_extractions

    document_id = await upload(client, sample_documents["PAYSLIP_LATEST_3"])
    await client.post(f"/documents/{document_id}/complete", json={})
    await client.post(
        f"/documents/{document_id}/review",
        json={"field": "net_salary", "value": "9,999.99", "actor_id": "u-1"},
    )

    async with session() as db:
        every = await load_extractions(db, document_id, current_only=False)
    versions = [row for row in every if row["field"] == "net_salary"]
    assert len(versions) == 2, "the original reading must survive the correction"
    assert any(row["superseded_by"] for row in versions)


async def test_correcting_a_field_the_document_does_not_have_is_refused(
    client: AsyncClient, sample_documents: dict[str, Path]
) -> None:
    document_id = await upload(client, sample_documents["IDENTITY"])
    await client.post(f"/documents/{document_id}/complete", json={})
    response = await client.post(
        f"/documents/{document_id}/review", json={"field": "net_salary", "value": "1.00", "actor_id": "u-1"}
    )
    assert response.status_code == 422
