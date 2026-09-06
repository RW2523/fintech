"""Field extraction with bounding-box anchoring (docs/07 §1.3).

Every extracted value is anchored to the OCR words it came from, so it carries a
box an officer can be shown and a confidence that reflects how well it was read.
A value with no anchor is reported with a reduced confidence rather than a
confident guess.

The extractor is deliberately pluggable: this OCR-and-layout implementation is
what runs today, and the vision-model route in P4 replaces it behind the same
interface without changing anything downstream.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

from app.ocr import Line, Word

__all__ = ["FIELD_SPECS", "ExtractedField", "extract_fields", "normalise"]

#: Confidence multiplier when a value could not be anchored to OCR words.
_UNANCHORED_PENALTY = 0.7
#: How close a label has to read before it counts as that label.
_LABEL_THRESHOLD = 78.0

_MONEY = re.compile(r"^-?[\d,]+\.\d{2}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PERIOD = re.compile(r"^\d{4}-\d{2}$")
_ID_NUMBER = re.compile(r"^[A-Z]{2}\d{7}$")
_STAFF_NUMBER = re.compile(r"^[A-Z$]N-\d{4,7}$")


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """How to find one field on a page."""

    name: str
    #: Label text to look for. Empty means the field is positional.
    label: str = ""
    #: Alternative spellings, for labels OCR reads inconsistently.
    aliases: tuple[str, ...] = ()
    #: Look on the following line when the value is not on the label's line.
    allow_next_line: bool = False
    #: Only consider lines that appear before the first line containing this.
    before_marker: str = ""
    #: Values must look like this, if given.
    pattern: re.Pattern[str] | None = None
    #: Which matching occurrence to take, for repeated sections.
    occurrence: int = 0
    #: Take the whole first line instead of looking for a label.
    first_line: bool = False
    #: Labels that end this field's value when they appear on the same line.
    stop_labels: tuple[str, ...] = ()
    #: A value must not read as one of these (guards against a near-miss label).
    not_label: tuple[str, ...] = ()
    #: Require the label to open the line. "Deductions" would otherwise match
    #: the second word of "Cooperative deduction".
    label_at_line_start: bool = False
    #: End the value when a token matches this, for labels OCR tends to drop.
    stop_pattern: re.Pattern[str] | None = None


@dataclass(frozen=True, slots=True)
class ExtractedField:
    name: str
    value: str | None
    normalised: Any
    confidence: float
    bbox: tuple[float, float, float, float] | None
    method: str = "ocr"

    def as_row(self) -> dict[str, Any]:
        return {
            "field": self.name,
            "value": self.value,
            "norm_value": self.normalised,
            "conf": round(self.confidence, 4),
            "bbox": list(self.bbox) if self.bbox else None,
            "method": self.method,
        }


#: docs/05 §2 names the critical fields; the rest are read because they are
#: what reconciliation and the officer's evidence panel need.
FIELD_SPECS: dict[str, tuple[FieldSpec, ...]] = {
    "PAYSLIP_LATEST_3": (
        FieldSpec("employer_name", first_line=True),
        FieldSpec("period", label="PERIOD", pattern=_PERIOD, occurrence=0),
        FieldSpec("period_prior_1", label="PERIOD", pattern=_PERIOD, occurrence=1),
        FieldSpec("period_prior_2", label="PERIOD", pattern=_PERIOD, occurrence=2),
        FieldSpec("pay_date", label="PAID", pattern=_DATE, occurrence=0),
        FieldSpec("gross_salary", label="Basic salary", pattern=_MONEY, occurrence=0),
        FieldSpec("net_salary", label="Net pay", pattern=_MONEY, occurrence=0),
        FieldSpec("net_salary_prior_1", label="Net pay", pattern=_MONEY, occurrence=1),
        FieldSpec("net_salary_prior_2", label="Net pay", pattern=_MONEY, occurrence=2),
        FieldSpec(
            "total_deductions", label="Deductions", pattern=_MONEY, occurrence=0, label_at_line_start=True
        ),
        # OCR often loses the "Staff number" label, so the number is found by
        # its own shape and used to end the name that precedes it.
        FieldSpec("employee_name", label="Employee", stop_pattern=_STAFF_NUMBER),
        FieldSpec("staff_number", label="", pattern=_STAFF_NUMBER),
    ),
    "IDENTITY": (
        FieldSpec("name", label="Name", stop_labels=("ID number",)),
        FieldSpec("id_number", label="ID number", pattern=_ID_NUMBER),
        FieldSpec("dob", label="Date of birth", pattern=_DATE),
        FieldSpec("expiry", label="Expires", pattern=_DATE),
    ),
    "EMPLOYMENT_CONFIRMATION": (
        FieldSpec("employer_name", first_line=True),
        # "Employee" alone would match "the employee's request" in the body.
        FieldSpec(
            "employee_name", label="Employee name", stop_labels=("Position",), before_marker="Employment type"
        ),
        FieldSpec("position", label="Position"),
        FieldSpec("start_date", label="Start date", pattern=_DATE),
        FieldSpec("monthly_salary", label="Monthly salary", pattern=_MONEY),
        # The letter is dated above the salutation; every later date belongs to
        # the employment itself.
        FieldSpec("letter_date", label="", pattern=_DATE, occurrence=0, before_marker="whom it may concern"),
    ),
    "BANK_STATEMENT_3M": (
        FieldSpec("account_holder", label="Account holder", stop_labels=("Account number",)),
        FieldSpec("account_number_masked", label="Account number"),
        FieldSpec("period", label="Statement period", stop_labels=("Currency",)),
        FieldSpec("closing_balance", label="Closing balance", pattern=_MONEY, allow_next_line=True),
    ),
    "PROVIDENT_FUND_STATEMENT": (
        FieldSpec("member_name", label="Member", stop_labels=("Fund number",)),
        FieldSpec("fund_number", label="Fund number"),
        FieldSpec("period", label="Period", stop_labels=("Employer",)),
        FieldSpec("closing_balance", label="Closing balance", pattern=_MONEY, allow_next_line=True),
    ),
}


def _matches_label(words: list[Word], start: int, label: str) -> int:
    """How many words at ``start`` spell ``label``, or 0."""
    wanted = label.split()
    if start + len(wanted) > len(words):
        return 0
    read = " ".join(w.text for w in words[start : start + len(wanted)])
    if fuzz.ratio(read.casefold(), label.casefold()) >= _LABEL_THRESHOLD:
        return len(wanted)
    return 0


def _span_bbox(words: list[Word]) -> tuple[float, float, float, float]:
    return (
        min(w.bbox[0] for w in words),
        min(w.bbox[1] for w in words),
        max(w.bbox[2] for w in words),
        max(w.bbox[3] for w in words),
    )


#: OCR emits curly quotes and dashes around values; strip them all.
_EDGE_NOISE = " :.,\u2019\u2018'\u201c\u201d|-\u2014"


def _clean(text: str) -> str:
    return text.strip(_EDGE_NOISE)


_THOUSANDS = re.compile(r"^\d{1,3}$")


def _rejoin_thousands(words: list[Word], index: int, value: str) -> tuple[str, list[Word]]:
    """OCR sometimes splits `2,195.45` into `2` and `195.45`.

    A money token of the form `ddd.dd` preceded by a bare one-to-three digit
    group is the tail of a larger number, not a number in its own right.
    """
    if index == 0 or "," in value:
        return value, [words[index]]
    previous = _clean(words[index - 1].text)
    if _THOUSANDS.match(previous) and len(value.split(".")[0]) <= 3:
        return f"{previous},{value}", [words[index - 1], words[index]]
    return value, [words[index]]


def _labels(spec: FieldSpec) -> tuple[str, ...]:
    return (spec.label, *spec.aliases) if spec.label else spec.aliases


def _value_after_label(line: Line, spec: FieldSpec, label: str) -> tuple[str, list[Word]] | None:
    """The words following ``label`` on this line, up to the next label."""
    words = list(line.words)
    for index in range(len(words)):
        if spec.label_at_line_start and index != 0:
            break
        consumed = _matches_label(words, index, label)
        if not consumed:
            continue

        tail = words[index + consumed :]
        collected: list[Word] = []
        position = 0
        while position < len(tail):
            stop = any(_matches_label(tail, position, stop_label) for stop_label in spec.stop_labels)
            if spec.stop_pattern is not None and spec.stop_pattern.match(_clean(tail[position].text)):
                stop = True
            if stop:
                break
            collected.append(tail[position])
            position += 1
            if spec.pattern is not None and collected:
                # a pattern field is a single token; stop as soon as one matches
                candidate = _clean(collected[-1].text)
                if spec.pattern.match(candidate):
                    absolute = index + consumed + position - 1
                    if spec.pattern is _MONEY:
                        return _rejoin_thousands(words, absolute, candidate)
                    return candidate, [collected[-1]]

        if not collected:
            continue
        text = _clean(" ".join(w.text for w in collected))
        if spec.pattern is not None and not spec.pattern.match(text):
            continue
        return text, collected
    return None


def _first_line_value(lines: list[Line]) -> tuple[str, list[Word]] | None:
    """The heading, with the logo's stray glyph dropped."""
    if not lines:
        return None
    words = [w for w in lines[0].words if len(w.text) > 1 or w.text.isdigit()]
    if not words:
        words = list(lines[0].words)
    # a logo often OCRs as a single stray letter at the start
    while len(words) > 1 and len(words[0].text) <= 1:
        words = words[1:]
    return _clean(" ".join(w.text for w in words)), words


def normalise(name: str, value: str | None) -> Any:
    """Amounts to Decimal-safe strings, dates to ISO, names casefolded."""
    if value is None:
        return None
    text = value.strip()
    if _MONEY.match(text):
        return text.replace(",", "")
    if _DATE.match(text) or _PERIOD.match(text):
        return text
    return " ".join(text.split())


def extract_fields(document_type: str, lines: list[Line], *, method: str = "ocr") -> list[ExtractedField]:
    """Read every field this document type declares."""
    specs = FIELD_SPECS.get(document_type, ())
    found: list[ExtractedField] = []

    for spec in specs:
        hits: list[tuple[str, list[Word]]] = []

        if spec.first_line:
            hit = _first_line_value(lines)
            if hit:
                hits.append(hit)
        elif not spec.label:
            # positional: the first token anywhere matching the pattern
            for line in lines:
                for word in line.words:
                    candidate = _clean(word.text)
                    if spec.pattern is not None and spec.pattern.match(candidate):
                        hits.append((candidate, [word]))
                        break
                if hits:
                    break
        else:
            searchable = lines
            if spec.before_marker:
                marker = next(
                    (
                        i
                        for i, line in enumerate(lines)
                        if fuzz.partial_ratio(spec.before_marker.casefold(), line.text.casefold()) >= 88
                    ),
                    len(lines),
                )
                searchable = lines[:marker]

            for position, line in enumerate(searchable):
                for label in _labels(spec):
                    hit = _value_after_label(line, spec, label)
                    if hit is None and spec.allow_next_line and position + 1 < len(searchable):
                        # the label sat alone on its line; the value is below it
                        joined = Line(
                            text=f"{line.text} {searchable[position + 1].text}",
                            words=(*line.words, *searchable[position + 1].words),
                        )
                        hit = _value_after_label(joined, spec, label)
                    if hit:
                        hits.append(hit)
                        break

        if len(hits) <= spec.occurrence:
            found.append(ExtractedField(spec.name, None, None, 0.0, None, method))
            continue

        text, words = hits[spec.occurrence]
        confidence = sum(w.confidence for w in words) / len(words) if words else 0.0
        bbox = _span_bbox(words) if words else None
        if bbox is None:
            confidence *= _UNANCHORED_PENALTY
        found.append(
            ExtractedField(
                name=spec.name,
                value=text or None,
                normalised=normalise(spec.name, text),
                confidence=round(confidence, 4),
                bbox=bbox,
                method=method,
            )
        )

    return found
