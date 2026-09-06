"""The output policy screen (docs/06 §9).

What an agent may not say, and what it may not have made up. Three checks, in
order of how badly they would fail a member:

* a claim carrying a number no tool produced is the model doing arithmetic,
  which CLAUDE.md §2.1 forbids outright;
* a claim with no evidence is an assertion dressed as a finding;
* language that promises an outcome, or mentions a protected characteristic,
  is wrong whatever the case.

Each returns the reason, because an opinion that is rejected has to be
explainable to whoever wrote the prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = ["FORBIDDEN_TERMS", "Screening", "screen_answer", "screen_opinion"]

#: docs/06 §9 and CLAUDE.md §5 — never mentioned, never inferred.
FORBIDDEN_TERMS: tuple[str, ...] = (
    "ethnic",
    "ethnicity",
    "race",
    "racial",
    "religion",
    "religious",
    "muslim",
    "christian",
    "hindu",
    "jewish",
    "gender",
    "male",
    "female",
    "pregnan",
    "disab",
    "health condition",
    "illness",
    "hiv",
    "political",
    "party",
    "union member",
    "sexual",
)

#: Language that states an outcome the agent does not decide, or promises one.
FORBIDDEN_PHRASES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("promise", re.compile(r"\b(guarantee[sd]?|guaranteed)\b", re.I)),
    ("predetermined_outcome", re.compile(r"\bwill (be )?(approved|declined|rejected|accepted)\b", re.I)),
    (
        "legal_notice",
        re.compile(
            r"\b(this constitutes|legally binding|without prejudice|"
            r"we hereby)\b",
            re.I,
        ),
    ),
    ("url", re.compile(r"https?://|\bwww\.\w", re.I)),
)

#: A number in a claim must have come from a tool. Years and identifiers are
#: allowed through: a year is not a computed quantity, and an id is checked by
#: the evidence rule instead.
_NUMBER = re.compile(r"(?<![\w.-])(\d[\d,]*(?:\.\d+)?)(?![\w-])")
_YEAR = re.compile(r"^(19|20)\d{2}$")


@dataclass
class Screening:
    """What the screen found. Empty means the opinion may stand."""

    rejections: list[dict[str, Any]] = field(default_factory=list)
    redactions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.rejections

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "rejections": self.rejections, "redactions": self.redactions}


def _numbers_in(text: str) -> set[str]:
    """Every quantity in a string, normalised so 2,201.15 matches 2201.15."""
    return {m.group(1).replace(",", "") for m in _NUMBER.finditer(text) if not _YEAR.match(m.group(1))}


def _tool_numbers(tool_results: Any) -> set[str]:
    """Every number any tool returned, at any depth."""
    found: set[str] = set()
    if isinstance(tool_results, (int, float)) and not isinstance(tool_results, bool):
        found.add(_normalise(tool_results))
    elif isinstance(tool_results, str):
        found |= _numbers_in(tool_results)
    elif isinstance(tool_results, dict):
        for key, value in tool_results.items():
            # Keys count as well as values. A tool named `ontime_rate_24m`
            # makes 24 available: an agent saying "over 24 months" is naming
            # the window it was given, not computing one.
            if isinstance(key, str):
                # A field name runs its digits into the unit, as in
                # `ontime_rate_24m`, so the digits are taken directly rather
                # than through the prose matcher.
                found |= set(re.findall(r"\d+", key))
            found |= _tool_numbers(value)
    elif isinstance(tool_results, list):
        for item in tool_results:
            found |= _tool_numbers(item)
    return found


def _normalise(value: float) -> str:
    text = f"{value}"
    return text[:-2] if text.endswith(".0") else text


def _rounded_forms(value: str) -> set[str]:
    """The ways a tool's number may legitimately be written in prose.

    A tool returning 0.4775 may be quoted as 0.48 or 0.477. Rounding is not
    computing, so those forms are accepted; anything else is not.
    """
    forms = {value}
    try:
        number = float(value)
    except ValueError:
        return forms
    for places in range(5):
        forms.add(_normalise(round(number, places)))
        forms.add(f"{number:.{places}f}")
    forms.add(f"{number:,.2f}".replace(",", ""))
    if number == int(number):
        forms.add(str(int(number)))
    # A ratio quoted as a percentage: 0.4775 as 47.75 or 48.
    for places in range(3):
        forms.add(_normalise(round(number * 100, places)))
    return forms


def screen_opinion(
    opinion: dict[str, Any], *, tool_results: Any = None, evidence_ids: set[str] | None = None
) -> Screening:
    """Check one agent opinion against the output policy."""
    screening = Screening()
    known_numbers: set[str] = set()
    for value in _tool_numbers(tool_results or []):
        known_numbers |= _rounded_forms(value)
    available = evidence_ids or set()

    for index, claim in enumerate(opinion.get("claims") or []):
        text = str(claim.get("text") or "")
        where = f"claims[{index}]"

        cited = [str(e) for e in (claim.get("evidence_refs") or [])]
        if not cited:
            screening.rejections.append(
                {
                    "where": where,
                    "rule": "claim_without_evidence",
                    "detail": "every claim must cite at least one evidence id "
                    "produced in this run (docs/06 §5.1 rule 1)",
                }
            )
        elif available and not set(cited) <= available:
            screening.rejections.append(
                {
                    "where": where,
                    "rule": "evidence_not_from_this_run",
                    "detail": f"cites {sorted(set(cited) - available)}, which no tool in this run produced",
                }
            )

        invented = sorted(_numbers_in(text) - known_numbers)
        if invented:
            screening.rejections.append(
                {
                    "where": where,
                    "rule": "number_not_from_a_tool",
                    "detail": f"states {invented}, which appears in no tool result "
                    "(CLAUDE.md §2.1: a model never computes a number)",
                }
            )

        screening.rejections.extend(_language_rejections(text, where))

    for field_name in ("stance", "reason_codes"):
        field_value: Any = opinion.get(field_name)
        if isinstance(field_value, str):
            screening.rejections.extend(_language_rejections(field_value, field_name))

    return screening


def _language_rejections(text: str, where: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    lowered = text.casefold()
    hits = [term for term in FORBIDDEN_TERMS if term in lowered]
    if hits:
        out.append({"where": where, "rule": "protected_characteristic", "detail": f"mentions {hits}"})
    for name, pattern in FORBIDDEN_PHRASES:
        if pattern.search(text):
            out.append({"where": where, "rule": name, "detail": f"matched {pattern.pattern}"})
    return out


def screen_answer(
    answer: dict[str, Any], *, tool_results: Any = None, evidence_ids: set[str] | None = None
) -> Screening:
    """Check one copilot answer against the output policy (docs/06 §9).

    The same rules as an opinion, applied to prose rather than to claims. A
    copilot answers an officer who is about to act, so the standard is not
    lower for being conversational: a number nobody produced and a citation
    nobody can open are the same defect whichever shape they arrive in.

    A refusal is screened too, and passes trivially: refusing to answer is
    always allowed and never needs evidence.
    """
    screening = Screening()
    text = str(answer.get("answer") or "")
    refusal = answer.get("refusal") or {}

    if refusal.get("reason"):
        # A refusal that also tried to answer is not a refusal. Everything
        # after "I cannot answer that, but" is the thing being refused.
        if text.strip():
            screening.rejections.append(
                {
                    "where": "answer",
                    "reason": "a refusal must not also answer the question",
                    "detail": text[:200],
                }
            )

        # A refusal that cites evidence is an answer wearing a refusal label.
        # Measured: the first model to see this screen refused with
        # NO_EVIDENCE while its reason named three gate rules and cited five
        # identifiers, which is the answer the officer asked for filed under a
        # heading that tells them to ignore it.
        if answer.get("citations"):
            screening.rejections.append(
                {
                    "where": "refusal",
                    "reason": "a refusal that cites evidence is an answer; put it in `answer`",
                    "detail": [str(c.get("ref")) for c in answer["citations"]][:5],
                }
            )

        # And a refusal is not a licence to state facts. One that reads "the
        # case has been declined by a senior officer" has told the officer
        # something, and told them without evidence, which is exactly what the
        # refusal was supposed to avoid. Measured: the first model to see this
        # screen did precisely that.
        why = str(refusal.get("reason") or "")
        screening.rejections.extend(_language_rejections(why, "refusal.reason"))

        refusal_numbers: set[str] = set()
        for value in _tool_numbers(tool_results or []):
            refusal_numbers |= _rounded_forms(value)
        invented_in_refusal = _numbers_in(why) - refusal_numbers
        if invented_in_refusal:
            screening.rejections.append(
                {
                    "where": "refusal.reason",
                    "reason": "states numbers no tool in this run produced",
                    "detail": sorted(invented_in_refusal)[:5],
                }
            )
        return screening

    if not text.strip():
        screening.rejections.append(
            {"where": "answer", "reason": "an answer that says nothing is not an answer"}
        )
        return screening

    screening.rejections.extend(_language_rejections(text, "answer"))

    known_numbers: set[str] = set()
    for value in _tool_numbers(tool_results or []):
        known_numbers |= _rounded_forms(value)
    invented = _numbers_in(text) - known_numbers
    if invented:
        screening.rejections.append(
            {
                "where": "answer",
                "reason": "states numbers no tool in this run produced",
                "detail": sorted(invented)[:5],
            }
        )

    citations = answer.get("citations") or []
    if not citations:
        # An answer with no citation is a claim the officer cannot check, and
        # the whole point of this copilot is that they can.
        screening.rejections.append({"where": "citations", "reason": "an answer must cite what it rests on"})

    available = evidence_ids or set()
    if available:
        unknown = [
            str(citation.get("ref")) for citation in citations if str(citation.get("ref")) not in available
        ]
        if unknown:
            screening.rejections.append(
                {
                    "where": "citations",
                    "reason": "cites identifiers no tool in this run produced",
                    "detail": unknown[:5],
                }
            )

    return screening
