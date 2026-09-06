"""Document classification (docs/07 §1.2).

The label set is fixed and small. A keyword prior over the OCR text decides
today; the vision route in P4 slots in behind the same interface, and the
documented rule applies then: take the model's label unless its confidence is
below 0.6 and the keyword prior is strong.

Anything below the confidence floor becomes a human classification task rather
than a guess the rest of the pipeline would trust.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ocr import Line

__all__ = ["CLASSIFICATION_FLOOR", "LABELS", "REQUIRED_TYPE", "Classification", "classify", "keyword_prior"]

#: docs/07 §1.2 — the constrained label set.
LABELS = (
    "IDENTITY",
    "PAYSLIP",
    "BANK_STATEMENT",
    "EMPLOYMENT_CONFIRMATION",
    "PROVIDENT_FUND_STATEMENT",
    "FINANCING_STATEMENT",
    "OTHER",
)

#: Classifier label -> the document type policy requires (docs/05 §2).
REQUIRED_TYPE = {
    "IDENTITY": "IDENTITY",
    "PAYSLIP": "PAYSLIP_LATEST_3",
    "BANK_STATEMENT": "BANK_STATEMENT_3M",
    "EMPLOYMENT_CONFIRMATION": "EMPLOYMENT_CONFIRMATION",
    "PROVIDENT_FUND_STATEMENT": "PROVIDENT_FUND_STATEMENT",
    "FINANCING_STATEMENT": "FINANCING_STATEMENT",
    "OTHER": "OTHER",
}

#: Below this the document goes to a person (docs/07 §1.2).
CLASSIFICATION_FLOOR = 0.85

#: Phrases that identify each label, with the weight each carries. They are
#: matched against the whole page text, casefolded.
_KEYWORDS: dict[str, tuple[tuple[str, float], ...]] = {
    "IDENTITY": (
        ("national identity card", 6.0),
        ("identity card", 4.0),
        ("id number", 3.0),
        ("date of birth", 2.0),
        ("expires", 1.0),
    ),
    "PAYSLIP": (
        ("statement of earnings", 6.0),
        ("net pay", 4.0),
        ("basic salary", 3.0),
        ("deductions", 2.0),
        ("pay period", 2.0),
        ("staff number", 2.0),
        ("payslip", 4.0),
    ),
    "BANK_STATEMENT": (
        ("account statement", 6.0),
        ("account holder", 4.0),
        ("closing balance", 3.0),
        ("salary credit", 2.0),
        ("statement period", 2.0),
        ("debit", 1.0),
    ),
    "EMPLOYMENT_CONFIRMATION": (
        ("to whom it may concern", 6.0),
        ("confirms the employment", 5.0),
        ("human resources", 3.0),
        ("employment type", 2.0),
        ("authorised signatory", 2.0),
    ),
    "PROVIDENT_FUND_STATEMENT": (
        ("provident fund statement", 6.0),
        ("provident fund", 4.0),
        ("fund number", 3.0),
        ("contributions", 1.0),
    ),
    "FINANCING_STATEMENT": (
        ("financing statement", 6.0),
        ("outstanding principal", 3.0),
        ("instalment schedule", 3.0),
    ),
}


@dataclass(frozen=True, slots=True)
class Classification:
    label: str
    confidence: float
    method: str
    #: The runner-up, which is what makes a low confidence interpretable.
    alternative: str | None = None
    needs_human: bool = False

    @property
    def required_type(self) -> str:
        return REQUIRED_TYPE.get(self.label, "OTHER")


def keyword_prior(text: str) -> dict[str, float]:
    """Score every label against the page text."""
    lowered = text.casefold()
    scores: dict[str, float] = {}
    for label, phrases in _KEYWORDS.items():
        scores[label] = sum(weight for phrase, weight in phrases if phrase in lowered)
    return scores


def classify(
    lines: list[Line],
    *,
    vision_label: str | None = None,
    vision_confidence: float = 0.0,
) -> Classification:
    """Decide what this document is.

    ``vision_*`` carry the model's answer once the vision route exists. Until
    then the keyword prior decides on its own.
    """
    text = "\n".join(line.text for line in lines)
    scores = keyword_prior(text)
    ranked = sorted(scores.items(), key=lambda item: -item[1])
    best, best_score = ranked[0]
    runner_up, runner_score = ranked[1] if len(ranked) > 1 else ("OTHER", 0.0)

    if best_score <= 0:
        return Classification("OTHER", 0.0, "keyword", None, needs_human=True)

    # Confidence is how far ahead the winner is, not how loud it shouted.
    margin = (best_score - runner_score) / best_score
    prior_confidence = round(min(0.99, 0.55 + 0.45 * margin), 4)
    # docs/07 §1.2 — the model's label wins unless it is unsure and the keyword
    # prior is confident enough to be worth deferring to.
    trust_vision = vision_label is not None and (vision_confidence >= 0.6 or prior_confidence < 0.75)

    if trust_vision:
        assert vision_label is not None
        confidence = round(max(vision_confidence, prior_confidence * 0.9), 4)
        return Classification(
            vision_label,
            confidence,
            "vision",
            alternative=best if best != vision_label else runner_up,
            needs_human=confidence < CLASSIFICATION_FLOOR,
        )

    return Classification(
        best,
        prior_confidence,
        "keyword",
        alternative=runner_up,
        needs_human=prior_confidence < CLASSIFICATION_FLOOR,
    )
