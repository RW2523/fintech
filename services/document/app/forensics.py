"""Document forensics (docs/07 §1.4).

Each check answers a different question about whether a page is what it claims
to be. None of them is proof on its own: they produce findings, which the fraud
service weighs and an officer reads. A finding says "observed", never "proven"
(docs/06 §5.5).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

__all__ = [
    "CRITICAL_FIELDS",
    "PHASH_THRESHOLD",
    "Finding",
    "check_arithmetic",
    "check_layout_signature",
    "check_metadata",
    "check_readability",
    "check_reused_image",
    "detect_copy_move",
    "run_forensics",
]

#: docs/05 §2 — the fields a decision rests on. A document that cannot be read
#: well enough to produce them is a DOC-04 problem, never a silent pass.
CRITICAL_FIELDS = {
    "PAYSLIP_LATEST_3": ("net_salary", "gross_salary", "employer_name", "period"),
    "IDENTITY": ("id_number", "name", "dob"),
    "EMPLOYMENT_CONFIRMATION": ("employer_name", "employee_name", "letter_date"),
}

#: Hamming distance on a 256-bit page hash below which two pages are candidates
#: for being the same image. Measured in T-022: unrelated documents of the same
#: type sit far above this, but the overlap is not zero, so a hash match is
#: confirmed against content before it becomes a finding.
PHASH_THRESHOLD = 6

#: A payslip that does not add up by more than this is worth a look. The floor
#: absorbs rounding; the proportional part absorbs a single mis-read digit,
#: which is far smaller than the 8-20 % a forger adds.
_ARITHMETIC_FLOOR = 1.0
_ARITHMETIC_SHARE_OF_GROSS = 0.02

#: Below this, a figure was not read well enough to accuse anyone with. A
#: document that cannot be read is a DOC-04 problem, not a fraud finding.
_MIN_INPUT_CONFIDENCE = 0.75

#: A payslip's net cannot be a sliver of its gross: deductions do not consume
#: nearly the whole pay packet, and a figure that says they did was misread,
#: not forged. Measured on this corpus, two clean payslips were accused on the
#: strength of a net read as 1.65 against a gross of 2,612 and 213 against
#: 2,201. Reading failure is a DOC-04 question, so the arithmetic and income
#: checks decline the comparison rather than making an accusation out of it.
_MIN_NET_SHARE_OF_GROSS = 0.10

_MONEY = re.compile(r"^-?[\d,]*\d(\.\d{2})?$")


@dataclass(frozen=True, slots=True)
class Finding:
    """One observation about a document (docs/03 §11 reason codes)."""

    code: str
    severity: str
    detail: dict[str, Any]
    document_id: str | None = None
    evidence_refs: tuple[str, ...] = field(default=())

    def as_row(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "detail": self.detail,
            "document_id": self.document_id,
            "evidence_refs": list(self.evidence_refs),
        }


def _amount(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not _MONEY.match(str(value).strip()):
        return None
    try:
        return float(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# arithmetic: the strongest signal that a figure was edited
# ---------------------------------------------------------------------------
def _declined(
    document_id: str | None,
    check: str,
    fields: list[str],
    *,
    why: str | None = None,
) -> Finding:
    """A check that could not be run, reported as a request for a clearer copy.

    DOC-04 asks the member for something legible. It is not an accusation, and
    it is the honest outcome when the figures a check needs were not read well
    enough to compare.
    """
    return Finding(
        code="DOC-04",
        severity="LOW",
        document_id=document_id,
        detail={
            "check": check,
            "declined_on": fields,
            "threshold": _MIN_INPUT_CONFIDENCE,
            "observed": why or ("the figures this check needs were not read confidently enough to compare"),
        },
    )


def _plausible_net(gross: float | None, net: float | None) -> bool:
    """Whether these two figures could have come off the same payslip.

    A net below a tenth of gross, or above it, is a reading failure. Comparing
    such a pair produces an accusation about the OCR rather than the document.
    """
    if gross is None or net is None or gross <= 0:
        return False
    return _MIN_NET_SHARE_OF_GROSS <= net / gross <= 1.0


def check_arithmetic(
    fields: dict[str, Any],
    *,
    document_id: str | None = None,
    confidences: dict[str, float] | None = None,
) -> list[Finding]:
    """A payslip must add up: net plus deductions equals gross.

    Someone who raises the net without recomputing the deduction line leaves
    the page inconsistent with itself, which no amount of retouching hides.
    """
    gross = _amount(fields.get("gross_salary"))
    net = _amount(fields.get("net_salary"))
    deductions = _amount(fields.get("total_deductions"))
    if gross is None or net is None or deductions is None:
        return []

    # Never accuse a document on the strength of a badly read number. Say so
    # rather than falling silent: a check that could not run is a reason to
    # ask for a clearer copy, and returning nothing would let the page pass as
    # though it had been examined.
    unreliable = sorted(
        name
        for name in ("gross_salary", "net_salary", "total_deductions")
        if (confidences or {}).get(name, 1.0) < _MIN_INPUT_CONFIDENCE
    )
    if unreliable:
        return [_declined(document_id, "payslip_arithmetic", unreliable)]
    if not _plausible_net(gross, net):
        return [
            _declined(
                document_id,
                "payslip_arithmetic",
                ["net_salary"],
                why="the stated net is not a plausible share of gross",
            )
        ]

    tolerance = max(_ARITHMETIC_FLOOR, _ARITHMETIC_SHARE_OF_GROSS * gross)
    deviation = round(net + deductions - gross, 2)
    if abs(deviation) <= tolerance:
        return []

    return [
        Finding(
            code="INT-01",
            # An overstated net is what a forger produces; an understated one is
            # more likely an OCR slip, so it is reported less loudly.
            severity="HIGH" if deviation > 0 else "MEDIUM",
            document_id=document_id,
            detail={
                "check": "payslip_arithmetic",
                "gross_salary": gross,
                "total_deductions": deductions,
                "net_salary": net,
                "deviation": deviation,
                "tolerance": round(tolerance, 2),
                "observed": (
                    "the stated net exceeds gross less deductions"
                    if deviation > 0
                    else "the stated net falls short of gross less deductions"
                ),
            },
        )
    ]


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------
def _as_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


#: A confirmation letter older than this when the application was made is
#: stale: the employment it certifies may no longer hold (docs/07 §1.4).
STALE_LETTER_DAYS = 45


def check_metadata(
    fields: dict[str, Any],
    *,
    document_id: str | None = None,
    created_at: date | None = None,
    application_date: date | None = None,
) -> list[Finding]:
    """A document cannot be dated before the period it certifies (docs/07 §1.4)."""
    findings: list[Finding] = []

    letter = _as_date(fields.get("letter_date"))
    if letter and application_date:
        age = (application_date - letter).days
        if age > STALE_LETTER_DAYS:
            findings.append(
                Finding(
                    code="INT-01",
                    severity="MEDIUM",
                    document_id=document_id,
                    detail={
                        "check": "stale_confirmation_letter",
                        "letter_date": letter.isoformat(),
                        "application_date": application_date.isoformat(),
                        "days_old": age,
                        "observed": (
                            f"the letter predates the application by more than {STALE_LETTER_DAYS} days"
                        ),
                    },
                )
            )
        elif age < 0:
            findings.append(
                Finding(
                    code="INT-01",
                    severity="MEDIUM",
                    document_id=document_id,
                    detail={
                        "check": "letter_dated_after_application",
                        "letter_date": letter.isoformat(),
                        "application_date": application_date.isoformat(),
                    },
                )
            )

    letter_date = _as_date(fields.get("letter_date"))
    period = str(fields.get("period") or "")[:7]
    if letter_date and re.match(r"^\d{4}-\d{2}$", period):
        period_start = _as_date(f"{period}-01")
        if period_start and letter_date < period_start:
            findings.append(
                Finding(
                    code="INT-01",
                    severity="MEDIUM",
                    document_id=document_id,
                    detail={
                        "check": "date_before_period",
                        "letter_date": letter_date.isoformat(),
                        "claimed_period": period,
                        "observed": "the document predates the period it certifies",
                    },
                )
            )

    pay_date = _as_date(fields.get("pay_date"))
    payslip_period = str(fields.get("period") or "")[:7]
    if (
        pay_date
        and re.match(r"^\d{4}-\d{2}$", payslip_period)
        and pay_date.strftime("%Y-%m") != payslip_period
    ):
        findings.append(
            Finding(
                code="INT-01",
                severity="MEDIUM",
                document_id=document_id,
                detail={
                    "check": "pay_date_outside_period",
                    "pay_date": pay_date.isoformat(),
                    "period": payslip_period,
                },
            )
        )

    if created_at and letter_date and created_at < letter_date:
        findings.append(
            Finding(
                code="INT-01",
                severity="MEDIUM",
                document_id=document_id,
                detail={
                    "check": "file_predates_content",
                    "file_created": created_at.isoformat(),
                    "document_date": letter_date.isoformat(),
                },
            )
        )

    return findings


# ---------------------------------------------------------------------------
# reused image
# ---------------------------------------------------------------------------
def _hamming(left: str, right: str) -> int:
    import imagehash

    return int(imagehash.hex_to_hash(left) - imagehash.hex_to_hash(right))


def check_reused_image(
    document: dict[str, Any],
    others: list[dict[str, Any]],
    *,
    content_key: str | None = None,
) -> list[Finding]:
    """The same page submitted by two members (docs/07 §1.4).

    A hash match alone is not enough: T-022 measured that documents sharing a
    layout can fall inside the threshold by themselves. An exact byte match, or
    a hash match confirmed by identical content, is what raises the finding.
    """
    findings: list[Finding] = []
    for other in others:
        if other.get("member_id") == document.get("member_id"):
            continue
        if other.get("type") != document.get("type"):
            continue

        if document.get("sha256") and document["sha256"] == other.get("sha256"):
            findings.append(
                Finding(
                    code="INT-02",
                    severity="HIGH",
                    document_id=document.get("document_id"),
                    detail={
                        "check": "identical_bytes",
                        "other_document_id": other.get("document_id"),
                        "other_member_id": other.get("member_id"),
                        "distance": 0,
                    },
                )
            )
            continue

        if not document.get("phash") or not other.get("phash"):
            continue
        distance = _hamming(document["phash"], other["phash"])
        if distance > PHASH_THRESHOLD:
            continue

        # a near-identical image is only a finding when the content agrees too
        same_content = (
            content_key is not None
            and document.get(content_key) is not None
            and document.get(content_key) == other.get(content_key)
        )
        findings.append(
            Finding(
                code="INT-02",
                # T-022 measured that documents sharing a layout can fall inside
                # the threshold on their own, so an unconfirmed hash match is a
                # lead to follow rather than a finding an officer must answer.
                severity="HIGH" if same_content else "LOW",
                document_id=document.get("document_id"),
                detail={
                    "check": "near_identical_image",
                    "other_document_id": other.get("document_id"),
                    "other_member_id": other.get("member_id"),
                    "distance": distance,
                    "content_confirmed": same_content,
                    "observed": "the same page appears under two members",
                },
            )
        )
    return findings


# ---------------------------------------------------------------------------
# layout signature
# ---------------------------------------------------------------------------
def check_layout_signature(
    *,
    expected_template: str | None,
    observed_template: str | None,
    document_id: str | None = None,
) -> list[Finding]:
    """An employer's payslips should carry that employer's layout (docs/07 §1.4)."""
    if not expected_template or not observed_template:
        return []
    if expected_template == observed_template:
        return []
    return [
        Finding(
            code="INT-07",
            severity="MEDIUM",
            document_id=document_id,
            detail={
                "check": "template_mismatch",
                "employer_template": expected_template,
                "observed_template": observed_template,
                "observed": "the layout is not this employer's",
            },
        )
    ]


def detect_copy_move(png: bytes, *, document_id: str | None = None, min_matches: int = 12) -> list[Finding]:
    """Regions of the page duplicated elsewhere on the same page (docs/07 §1.4).

    ORB keypoints matched against themselves: a retouched area is usually pasted
    from somewhere else on the page, which shows up as a cluster of matches with
    a consistent offset.
    """
    try:
        import cv2
        import numpy as np
        from PIL import Image
    except ImportError:
        return []

    image = np.array(Image.open(io.BytesIO(png)).convert("L"))
    orb = cv2.ORB_create(nfeatures=2000)  # type: ignore[attr-defined]
    keypoints, descriptors = orb.detectAndCompute(image, None)
    if descriptors is None or len(keypoints) < min_matches * 2:
        return []

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(descriptors, descriptors, k=3)

    offsets: list[tuple[int, int]] = []
    for group in pairs:
        for match in group[1:]:  # skip the self-match
            a = keypoints[match.queryIdx].pt
            b = keypoints[match.trainIdx].pt
            dx, dy = int(b[0] - a[0]), int(b[1] - a[1])
            if abs(dx) + abs(dy) < 24:  # neighbouring texture, not a copy
                continue
            offsets.append((dx // 8, dy // 8))

    if not offsets:
        return []

    from collections import Counter

    ((offset, count),) = Counter(offsets).most_common(1)
    if count < min_matches:
        return []

    return [
        Finding(
            code="INT-01",
            severity="MEDIUM",
            document_id=document_id,
            detail={
                "check": "copy_move",
                "matched_keypoints": count,
                "offset": list(offset),
                "observed": "a region of the page repeats elsewhere on it",
            },
        )
    ]


def run_forensics(
    *,
    document: dict[str, Any],
    fields: dict[str, Any],
    others: list[dict[str, Any]] | None = None,
    expected_template: str | None = None,
    observed_template: str | None = None,
) -> list[Finding]:
    """Every check that needs only this document and its neighbours."""
    document_id = document.get("document_id")
    findings: list[Finding] = []
    findings += check_arithmetic(fields, document_id=document_id)
    findings += check_metadata(fields, document_id=document_id)
    findings += check_reused_image(document, others or [], content_key="account_holder")
    findings += check_layout_signature(
        expected_template=expected_template, observed_template=observed_template, document_id=document_id
    )
    return findings


def check_readability(
    document_type: str,
    fields: dict[str, Any],
    confidences: dict[str, float] | None = None,
    *,
    minimum: float = 0.85,
    document_id: str | None = None,
) -> list[Finding]:
    """A critical field that could not be read routes the case to a person.

    CLAUDE.md §2.7: nothing is approved because something failed. A document
    whose figures cannot be read is reported, not quietly accepted.
    """
    critical = CRITICAL_FIELDS.get(document_type, ())
    if not critical:
        return []

    unread = [name for name in critical if not fields.get(name)]
    faint = [name for name in critical if fields.get(name) and (confidences or {}).get(name, 1.0) < minimum]
    if not unread and not faint:
        return []

    return [
        Finding(
            code="DOC-04",
            severity="MEDIUM",
            document_id=document_id,
            detail={
                "check": "critical_field_unreadable",
                "unread": unread,
                "below_confidence": faint,
                "minimum_confidence": minimum,
                "observed": ("a field the decision depends on could not be read with confidence"),
            },
        )
    ]
