"""T-023 — ingest checks, classification and extraction (docs/07 §1.1-§1.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

import cio_contracts
from app.classify import CLASSIFICATION_FLOOR, LABELS, REQUIRED_TYPE, classify
from app.extract import FIELD_SPECS, normalise
from app.ocr import ocr_available
from app.pipeline import check_upload, evidence_for_field, process
from app.storage import ALLOWED_MIME, MAX_BYTES
from cio_common.errors import ValidationFailed

pytestmark = pytest.mark.skipif(not ocr_available(), reason="Tesseract not available")


# ---------------------------------------------------------------------------
# ingest checks
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mime", sorted(ALLOWED_MIME))
def test_permitted_types_are_accepted(mime: str) -> None:
    check_upload(mime=mime, size=1024)


@pytest.mark.parametrize(
    "mime", ["application/zip", "text/html", "image/svg+xml", "application/x-executable"]
)
def test_other_types_are_refused(mime: str) -> None:
    with pytest.raises(ValidationFailed, match="unsupported MIME"):
        check_upload(mime=mime, size=1024)


def test_an_oversized_file_is_refused() -> None:
    with pytest.raises(ValidationFailed, match="over the"):
        check_upload(mime="image/png", size=MAX_BYTES + 1)


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(ValidationFailed, match="empty"):
        check_upload(mime="image/png", size=0)


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------
def test_every_generated_type_classifies_correctly(sample_documents: dict[str, Path]) -> None:
    for document_type, path in sample_documents.items():
        result = process(path.read_bytes(), mime="image/png")
        assert result.classification.required_type == document_type, (
            f"{path.name} classified as {result.classification.required_type}"
        )
        assert result.classification.confidence >= CLASSIFICATION_FLOOR


def test_an_unrecognisable_page_goes_to_a_human() -> None:
    """docs/07 §1.2 — below the floor is a task, not a guess."""
    from app.ocr import Line, Word

    blank = [Line(text="lorem ipsum dolor sit amet", words=(Word("lorem", 0.9, (0, 0, 0.1, 0.1), 0),))]
    result = classify(blank)
    assert result.label == "OTHER"
    assert result.needs_human is True


def test_the_label_set_is_the_documented_one() -> None:
    assert set(LABELS) == {
        "IDENTITY",
        "PAYSLIP",
        "BANK_STATEMENT",
        "EMPLOYMENT_CONFIRMATION",
        "PROVIDENT_FUND_STATEMENT",
        "FINANCING_STATEMENT",
        "OTHER",
    }
    assert REQUIRED_TYPE["PAYSLIP"] == "PAYSLIP_LATEST_3"


def test_a_confident_vision_label_wins_over_the_keyword_prior() -> None:
    """docs/07 §1.2 — the model decides unless it is unsure."""
    from app.ocr import Line, Word

    lines = [
        Line(
            text="Basic salary Net pay statement of earnings",
            words=(Word("Basic", 0.9, (0, 0, 0.1, 0.1), 0),),
        )
    ]
    result = classify(lines, vision_label="BANK_STATEMENT", vision_confidence=0.93)
    assert result.label == "BANK_STATEMENT"
    assert result.method == "vision"


def test_an_unsure_vision_label_defers_to_a_strong_prior() -> None:
    from app.ocr import Line, Word

    lines = [
        Line(
            text="statement of earnings net pay basic salary staff number",
            words=(Word("statement", 0.9, (0, 0, 0.1, 0.1), 0),),
        )
    ]
    result = classify(lines, vision_label="OTHER", vision_confidence=0.2)
    assert result.label == "PAYSLIP"
    assert result.method == "keyword"


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------
def test_critical_fields_are_declared_for_the_gated_types() -> None:
    """docs/05 §2 names these; extraction must know how to find them."""
    payslip = {spec.name for spec in FIELD_SPECS["PAYSLIP_LATEST_3"]}
    assert {"net_salary", "gross_salary", "employer_name", "period"} <= payslip
    identity = {spec.name for spec in FIELD_SPECS["IDENTITY"]}
    assert {"id_number", "name", "dob"} <= identity


def test_every_extracted_field_has_a_box_and_a_confidence(sample_documents: dict[str, Path]) -> None:
    """T-023 acceptance: each field has bbox and confidence."""
    for path in sample_documents.values():
        result = process(path.read_bytes(), mime="image/png")
        for extracted in result.fields:
            if extracted.value is None:
                continue
            assert extracted.bbox is not None, f"{extracted.name} has no box"
            assert 0.0 < extracted.confidence <= 1.0
            assert all(0.0 <= v <= 1.0 for v in extracted.bbox)


def test_a_payslip_reads_its_three_periods(sample_documents: dict[str, Path]) -> None:
    result = process(sample_documents["PAYSLIP_LATEST_3"].read_bytes(), mime="image/png")
    values = {f.name: f.value for f in result.fields}
    for name in (
        "net_salary",
        "net_salary_prior_1",
        "net_salary_prior_2",
        "period",
        "period_prior_1",
        "period_prior_2",
    ):
        assert values.get(name), f"{name} was not read"


def test_extraction_matches_the_ground_truth_for_critical_fields(sample_documents: dict[str, Path]) -> None:
    """Spot check; the corpus-wide measurement lives in synthetic/accuracy.py."""
    from tests.conftest import CORPUS

    truth = {
        json.loads(line)["document_id"]: json.loads(line)["fields"]
        for line in (CORPUS / "ground_truth.jsonl").read_text().splitlines()
    }
    documents = {
        json.loads(line)["filename"]: json.loads(line)
        for line in (CORPUS / "documents.jsonl").read_text().splitlines()
    }

    path = sample_documents["IDENTITY"]
    expected = truth[documents[path.name]["document_id"]]
    result = process(path.read_bytes(), mime="image/png")
    values = {f.name: f.value for f in result.fields}
    assert values["id_number"] == expected["id_number"]
    assert values["dob"] == expected["dob"]


def test_money_and_dates_are_normalised() -> None:
    assert normalise("net_salary", "2,195.45") == "2195.45"
    assert normalise("dob", "1990-12-27") == "1990-12-27"
    assert normalise("name", "  Tui   Sanuro ") == "Tui Sanuro"
    assert normalise("anything", None) is None


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------
def test_every_read_field_produces_a_valid_evidence_ref(sample_documents: dict[str, Path]) -> None:
    result = process(sample_documents["PAYSLIP_LATEST_3"].read_bytes(), mime="image/png")
    assert result.evidence
    for reference in result.evidence:
        cio_contracts.validate(reference, "EvidenceRef")
        assert reference["type"] == "DOCUMENT_FIELD"
        assert reference["locator"]["document_id"] == result.document_id
        assert "bbox" in reference["locator"]


def test_evidence_carries_the_permitted_uses_the_tools_check() -> None:
    from app.extract import ExtractedField

    reference = evidence_for_field(
        "doc_1", ExtractedField("net_salary", "2,195.45", "2195.45", 0.9, (0.1, 0.2, 0.3, 0.25))
    )
    assert set(reference["permitted_uses"]) == {"UNDERWRITING", "FRAUD"}


# ---------------------------------------------------------------------------
# hashing and pages
# ---------------------------------------------------------------------------
def test_the_same_bytes_hash_the_same_way(sample_documents: dict[str, Path]) -> None:
    data = sample_documents["IDENTITY"].read_bytes()
    first, second = process(data, mime="image/png"), process(data, mime="image/png")
    assert first.sha256 == second.sha256
    assert first.phash == second.phash


def test_a_page_render_is_produced(sample_documents: dict[str, Path]) -> None:
    import io

    result = process(sample_documents["IDENTITY"].read_bytes(), mime="image/png")
    assert result.page_png
    assert Image.open(io.BytesIO(result.page_png)).size[0] > 100
