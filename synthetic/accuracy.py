"""Measuring extraction and classification against the ground truth (T-023).

The corpus knows what every field says and where it sits, so accuracy here is
measured, not estimated. docs/00 T-023 sets the bar: critical fields at least
95 % exact, classification at least 98 %, and every field carrying a box and a
confidence.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["CRITICAL_FIELDS", "Accuracy", "measure"]

#: docs/05 §2 — the fields a decision actually rests on.
CRITICAL_FIELDS = {
    "PAYSLIP_LATEST_3": ("net_salary", "gross_salary", "employer_name", "period"),
    "IDENTITY": ("id_number", "name", "dob"),
}

_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Accuracy:
    documents: int = 0
    classification_correct: int = 0
    fields_total: int = 0
    fields_exact: int = 0
    critical_total: int = 0
    critical_exact: int = 0
    with_bbox: int = 0
    with_confidence: int = 0
    by_field: dict[str, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))
    misses: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        def rate(hit: int, total: int) -> float:
            return round(hit / total, 4) if total else 0.0

        return {
            "documents": self.documents,
            "classification_accuracy": rate(self.classification_correct, self.documents),
            "field_accuracy": rate(self.fields_exact, self.fields_total),
            "critical_field_accuracy": rate(self.critical_exact, self.critical_total),
            "bbox_coverage": rate(self.with_bbox, self.fields_total),
            "confidence_coverage": rate(self.with_confidence, self.fields_total),
            "by_field": {
                name: {"exact": hit, "total": total, "accuracy": rate(hit, total)}
                for name, (hit, total) in sorted(self.by_field.items())
            },
            "sample_misses": self.misses[:20],
        }


def _comparable(name: str, value: Any) -> str:
    """Normalise for comparison: money without separators, text casefolded."""
    if value is None:
        return ""
    text = str(value).strip().replace(",", "")
    return " ".join(text.casefold().split())


def measure(
    corpus: Path,
    *,
    limit: int | None = None,
    types: tuple[str, ...] | None = None,
) -> Accuracy:
    """Run OCR, classification and extraction over the corpus and score them."""
    sys.path.insert(0, str(_ROOT / "services" / "document"))
    from PIL import Image

    from app.classify import classify
    from app.extract import extract_fields
    from app.ocr import read_lines

    documents = [json.loads(line) for line in (corpus / "documents.jsonl").read_text().splitlines()]
    truth = {
        json.loads(line)["document_id"]: json.loads(line)
        for line in (corpus / "ground_truth.jsonl").read_text().splitlines()
    }

    if types:
        documents = [d for d in documents if d["type"] in types]
    if limit:
        documents = documents[:limit]

    result = Accuracy()

    for document in documents:
        image = Image.open(corpus / "files" / document["filename"])
        lines = read_lines(image)

        classification = classify(lines)
        result.documents += 1
        if classification.required_type == document["type"]:
            result.classification_correct += 1

        expected = truth[document["document_id"]]["fields"]
        critical = CRITICAL_FIELDS.get(document["type"], ())

        for extracted in extract_fields(document["type"], lines):
            if extracted.name not in expected:
                continue
            wanted = _comparable(extracted.name, expected[extracted.name])
            got = _comparable(extracted.name, extracted.value)
            hit = wanted == got

            result.fields_total += 1
            result.fields_exact += int(hit)
            result.by_field[extracted.name][1] += 1
            result.by_field[extracted.name][0] += int(hit)
            result.with_bbox += int(extracted.bbox is not None)
            result.with_confidence += int(extracted.confidence > 0)

            if extracted.name in critical:
                result.critical_total += 1
                result.critical_exact += int(hit)

            if not hit and len(result.misses) < 100:
                result.misses.append(
                    {
                        "document_id": document["document_id"],
                        "type": document["type"],
                        "field": extracted.name,
                        "expected": expected[extracted.name],
                        "extracted": extracted.value,
                    }
                )

    return result
