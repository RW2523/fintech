"""T-022 — the document corpus, its ground truth and its injected anomalies.

The corpus is expensive to render, so the tests build a small one of their own
rather than depending on whatever is in `synthetic/out`.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import imagehash
import pytest

from synthetic.documents.anomalies import INJECTED_SHARE, REASON_CODE, SEVERITY, AnomalyKind
from synthetic.documents.generate import DOCUMENT_TYPES, Corpus, generate_documents
from synthetic.documents.noise import ScanProfile, apply_scan_noise
from synthetic.documents.render import TEMPLATE_DIR, Renderer

POPULATION = Path("synthetic/out")

pytestmark = pytest.mark.skipif(
    not (POPULATION / "member.jsonl").is_file(),
    reason="no population on disk; run `synthetic.cli population` first",
)


#: The generated corpus and the directory it was written to.
Generated = tuple[Corpus, Path]


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Generated:
    out = tmp_path_factory.mktemp("documents")
    written = generate_documents(60, population_dir=POPULATION, out=out, seed=42, render_pdf=False)
    return written, out


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------
def test_twelve_payslip_layouts_exist() -> None:
    """docs/10 §6 — twelve employer templates, so a mismatch is detectable."""
    assert len(list(TEMPLATE_DIR.glob("payslip_tpl-*.html.j2"))) == 12


def test_every_document_type_has_a_template() -> None:
    for name in ("bank_statement", "identity_card", "employment_letter", "provident_fund_statement"):
        assert (TEMPLATE_DIR / f"{name}.html.j2").is_file()


def test_the_payslip_layouts_are_structurally_different() -> None:
    """A layout signature check needs real differences, not just colours."""
    bodies = [p.read_text() for p in sorted(TEMPLATE_DIR.glob("payslip_tpl-*.html.j2"))]
    assert len(set(bodies)) == 12
    fonts = {b.split("font-family: ")[1].split(";")[0] for b in bodies}
    assert len(fonts) >= 3, "layouts should not all share one typeface"


def test_a_rendered_page_reports_every_field_position() -> None:
    context = {
        "employer_name": "Northern Utilities 001",
        "template_id": "tpl-01",
        "employee_name": "Tees Tevoris",
        "staff_number": "SN-000042",
        "currency": "LCU",
        "net_style": "",
        "periods": [
            {
                "period": f"2026-0{m}",
                "pay_date": f"2026-0{m}-26",
                "gross_salary": "3,200.00",
                "net_salary": "2,340.97",
                "total_deductions": "859.03",
                "allowances": [{"label": "Transport", "amount": "150.00"}],
                "deductions": [{"label": "Pension", "amount": "256.00"}],
            }
            for m in (4, 3, 2)
        ],
    }
    with Renderer() as renderer:
        result = renderer.render("payslip_tpl-01.html.j2", context, pdf=False)

    assert {
        "employer_name",
        "employee_name",
        "period",
        "gross_salary",
        "net_salary",
        "total_deductions",
    } <= set(result.boxes)
    for name, box in result.boxes.items():
        assert len(box) == 4, name
        assert all(0.0 <= v <= 1.0 for v in box), f"{name} bbox is not normalised: {box}"
        assert box[0] < box[2] and box[1] < box[3], f"{name} bbox is inverted"


# ---------------------------------------------------------------------------
# the corpus
# ---------------------------------------------------------------------------
def test_every_application_gets_a_bundle(corpus: Generated) -> None:
    generated, _ = corpus
    assert len(generated.applications) == 60
    with_documents = {d.application_id for d in generated.documents}
    assert len(with_documents) == 60


def test_every_document_is_one_of_the_documented_types(corpus: Generated) -> None:
    generated, _ = corpus
    assert {d.type for d in generated.documents} <= set(DOCUMENT_TYPES)


def test_identity_is_always_present(corpus: Generated) -> None:
    """The bundle may be incomplete, but never without an identity document."""
    generated, _ = corpus
    by_application: dict[str, set[str]] = {}
    for document in generated.documents:
        by_application.setdefault(document.application_id, set()).add(document.type)
    assert all("IDENTITY" in types for types in by_application.values())


def test_some_bundles_are_deliberately_incomplete(corpus: Generated) -> None:
    """docs/10 §7 — a tenth are missing a required document."""
    generated, _ = corpus
    by_application: dict[str, set[str]] = {}
    for document in generated.documents:
        by_application.setdefault(document.application_id, set()).add(document.type)
    incomplete = [
        app
        for app, types in by_application.items()
        if not {"PAYSLIP_LATEST_3", "EMPLOYMENT_CONFIRMATION"} <= types
    ]
    assert incomplete, "no incomplete bundle was generated"


def test_every_document_has_ground_truth(corpus: Generated) -> None:
    """T-022 acceptance: ground truth present for all."""
    generated, out = corpus
    truth = {
        json.loads(line)["document_id"] for line in (out / "ground_truth.jsonl").read_text().splitlines()
    }
    assert {d.document_id for d in generated.documents} == truth


def test_every_document_has_bounding_boxes(corpus: Generated) -> None:
    """Extraction accuracy is only measurable against a known position."""
    generated, _ = corpus
    for document in generated.documents:
        assert document.boxes, f"{document.document_id} has no field positions"


def test_payslip_ground_truth_carries_all_three_periods(corpus: Generated) -> None:
    """Reconciliation takes the median net over three periods (docs/07 §1.5)."""
    generated, _ = corpus
    payslips = [d for d in generated.documents if d.type == "PAYSLIP_LATEST_3"]
    assert payslips
    for document in payslips:
        for field in (
            "net_salary",
            "net_salary_prior_1",
            "net_salary_prior_2",
            "period",
            "period_prior_1",
            "period_prior_2",
        ):
            assert field in document.ground_truth, f"{document.document_id} lacks {field}"


def test_critical_payslip_fields_are_present(corpus: Generated) -> None:
    """docs/05 §2 lists these as the critical fields."""
    generated, _ = corpus
    for document in generated.documents:
        if document.type != "PAYSLIP_LATEST_3":
            continue
        for field in ("net_salary", "gross_salary", "employer_name", "period"):
            assert field in document.ground_truth
            assert field in document.boxes


def test_files_are_written_for_every_document(corpus: Generated) -> None:
    generated, out = corpus
    for document in generated.documents:
        assert (out / "files" / document.filename).is_file()


def test_about_a_tenth_of_the_corpus_is_clean_digital(corpus: Generated) -> None:
    generated, _ = corpus
    clean = sum(1 for d in generated.documents if d.scan.is_clean)
    share = clean / len(generated.documents)
    assert 0.02 < share < 0.22, f"clean digital share is {share:.1%}, expected about 10%"


# ---------------------------------------------------------------------------
# anomalies
# ---------------------------------------------------------------------------
def test_the_manifest_lists_every_injected_anomaly(corpus: Generated) -> None:
    """T-022 acceptance: the anomaly manifest lists the injected cases."""
    generated, out = corpus
    manifest = json.loads((out / "anomalies.json").read_text())
    assert len(manifest) == len(generated.anomalies)
    for row in manifest:
        assert set(row) >= {
            "kind",
            "document_id",
            "application_id",
            "member_id",
            "reason_code",
            "expected_severity",
            "detail",
        }


def test_anomalies_point_at_documents_that_exist(corpus: Generated) -> None:
    generated, _ = corpus
    known = {d.document_id for d in generated.documents}
    assert all(a.document_id in known for a in generated.anomalies)


def test_the_injection_rate_is_roughly_as_configured(corpus: Generated) -> None:
    generated, _ = corpus
    affected = {a.application_id for a in generated.anomalies}
    share = len(affected) / len(generated.applications)
    assert 0.02 < share < INJECTED_SHARE * 2.5


def test_every_anomaly_kind_maps_to_a_reason_code_and_severity() -> None:
    for kind in AnomalyKind:
        assert kind in REASON_CODE, f"{kind} has no reason code"
        assert SEVERITY[kind] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def test_an_edited_total_is_recorded_with_its_original(corpus: Generated) -> None:
    generated, _ = corpus
    edited = [a for a in generated.anomalies if a.kind is AnomalyKind.EDITED_TOTAL]
    for anomaly in edited:
        assert anomaly.detail["stated_net"] > anomaly.detail["original_net"]
        assert 0.05 < anomaly.detail["inflation"] < 0.25


def test_an_identity_mismatch_records_both_names(corpus: Generated) -> None:
    generated, _ = corpus
    for anomaly in generated.anomalies:
        if anomaly.kind is AnomalyKind.IDENTITY_MISMATCH:
            assert anomaly.detail["record_name"] != anomaly.detail["document_name"]


# ---------------------------------------------------------------------------
# hashing, on which the reused-image check depends
# ---------------------------------------------------------------------------
def test_a_reused_image_hashes_identically(corpus: Generated) -> None:
    """The whole point: the same page under two members must collide."""
    generated, _ = corpus
    reused = [a for a in generated.anomalies if a.kind is AnomalyKind.REUSED_IMAGE]
    if not reused:
        pytest.skip("no reuse injected in this sample")

    by_id = {d.document_id: d for d in generated.documents}
    for anomaly in reused:
        document = by_id[anomaly.document_id]
        source = anomaly.detail["reused_from_member"]
        originals = [d for d in generated.documents if d.member_id == source and d.type == document.type]
        assert originals
        assert any(o.phash == document.phash for o in originals)


def test_unrelated_documents_of_the_same_type_are_mostly_far_apart(corpus: Generated) -> None:
    """A 64-bit page hash cannot separate documents; 256 bits mostly can.

    The residual overlap is why T-024 must confirm a hash candidate against
    extracted content rather than raising a finding on the hash alone.
    """
    generated, _ = corpus
    cards = [d for d in generated.documents if d.type == "IDENTITY"]
    pairs = [
        imagehash.hex_to_hash(a.phash) - imagehash.hex_to_hash(b.phash)
        for a, b in itertools.combinations(cards, 2)
        if a.member_id != b.member_id
    ]
    close = sum(1 for d in pairs if d <= 6)
    assert close / len(pairs) < 0.10, f"{close}/{len(pairs)} unrelated identity cards collide at threshold 6"


def test_documents_carry_a_content_hash(corpus: Generated) -> None:
    generated, _ = corpus
    for document in generated.documents:
        assert len(document.sha256) == 64
        assert document.phash


# ---------------------------------------------------------------------------
# scan noise
# ---------------------------------------------------------------------------
def test_scan_noise_changes_the_image_but_keeps_its_size() -> None:
    import io

    from PIL import Image

    original = Image.new("RGB", (400, 300), "white")
    for x in range(0, 400, 8):
        for y in range(0, 300, 8):
            original.putpixel((x, y), (20, 20, 20))
    buffer = io.BytesIO()
    original.save(buffer, format="PNG")
    raw = buffer.getvalue()

    noisy = apply_scan_noise(raw, ScanProfile(1.2, 0.6, 75, 1.02, 1.05))
    assert noisy != raw
    assert Image.open(io.BytesIO(noisy)).size == (400, 300)


def test_a_clean_profile_is_recognisable_as_clean() -> None:
    assert ScanProfile.clean().is_clean
    assert not ScanProfile(1.0, 0.0, 90, 1.0, 1.0).is_clean
