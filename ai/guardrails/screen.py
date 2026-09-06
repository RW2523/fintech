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

__all__ = [
    "FORBIDDEN_TERMS",
    "Screening",
    "numbers_a_tool_supplied",
    "numbers_in_prose",
    "rounded_forms",
    "screen_answer",
    "screen_opinion",
]

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


def numbers_in_prose(text: str) -> set[str]:
    """Every quantity in a string, normalised so 2,201.15 matches 2201.15.

    The hyphen in the lookbehind is what keeps `AFF-01` out: a rule id is not a
    quantity, and an agent citing the clause it read must not be accused of
    inventing the number one. Public because the evaluation harness grades the
    same thing: its own copy of this rule omitted the hyphen and reported four
    claims as inventing "01" and "03".
    """
    return {m.group(1).replace(",", "") for m in _NUMBER.finditer(text) if not _YEAR.match(m.group(1))}


_numbers_in = numbers_in_prose


def numbers_a_tool_supplied(tool_results: Any) -> set[str]:
    """Every number any tool returned, at any depth.

    Public because the evaluation harness grades the same thing and must apply
    the same rule. A harness with its own copy drifts from the screen it is
    grading: its version used the prose matcher, which does not see the digits
    inside a field name, so it reported "over the last 12 months" as an
    invented number when `arrears_12m` had supplied it.
    """
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
            found |= numbers_a_tool_supplied(value)
    elif isinstance(tool_results, list):
        for item in tool_results:
            found |= numbers_a_tool_supplied(item)
    return found


def _normalise(value: float) -> str:
    text = f"{value}"
    return text[:-2] if text.endswith(".0") else text


def rounded_forms(value: str) -> set[str]:
    """The ways a tool's number may legitimately be written in prose.

    A tool returning 0.4775 may be quoted as 0.48 or 0.477, and 12000 written
    as money is 12000.00. Rounding and formatting are not computing, so those
    forms are accepted; anything else is not.

    Public because the evaluation harness grades the same thing. Its own copy
    did not generate the two-decimal money form, so an agent quoting a product
    ceiling of 150000.00 exactly as the pack states it was reported as having
    invented the number.
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
    for value in numbers_a_tool_supplied(tool_results or []):
        known_numbers |= rounded_forms(value)

    # None means the caller cannot check. An empty set means the run produced
    # no evidence, which is not the same thing and is not permission to cite
    # anything: `available or set()` collapsed the two, so the check below was
    # skipped exactly when it mattered most.
    #
    # Measured on the golden set: `member_relationship` on S3, S4 and S5 is
    # given no tool results at all, invented four evidence ids that look like
    # real ones, and the screen passed it with no rejections. An agent with
    # nothing to go on citing four sources is the single worst output this
    # platform can produce, and this is the component whose job is to catch it.
    checking = evidence_ids is not None
    available = evidence_ids if evidence_ids is not None else set()

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
        elif checking and not set(cited) <= available:
            screening.rejections.append(
                {
                    "where": where,
                    "rule": "evidence_not_from_this_run",
                    "detail": f"cites {sorted(set(cited) - available)}, which no tool in this run produced"
                    + ("; this run produced none" if not available else ""),
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


#: Words that assert a movement over time. An answer using one is claiming the
#: numbers changed, which is a claim about a series rather than about a figure.
_TREND = re.compile(
    r"\b(fell|fall(en|ing)?|rose|risen|rising|increase[ds]?|increasing|"
    r"decrease[ds]?|decreasing|dropp?(ed|ing)?|declin(e|ed|ing)|"
    r"grew|grown|growing|improv(ed|ing)|worsen(ed|ing)?|"
    r"trend(ing)?|up from|down from|compared with last|month on month)\b",
    re.IGNORECASE,
)


def _series_points(tool_results: Any) -> int:
    """The shortest series any metric in this run returned, or 0 if none did.

    The shortest rather than the longest, deliberately. An answer claiming a
    movement does not say which metric it moved, so the run is only safe for
    trend claims when every series in it has something to move between. Taking
    the longest let a claim about approvals ride on delinquency's three months
    while the approvals series held a single point.

    Asked why approvals had fallen when every decision on file was from one
    month, the manager copilot reported a fall from 0.5 to 0.25, where 0.25 was
    the autonomous share, and then explained it. Both numbers were in the
    context, so the numeric screen passed: the invention was the relationship,
    not the figures.
    """
    lengths: list[int] = []
    for entry in tool_results or []:
        if not isinstance(entry, dict):
            continue
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        series = result.get("series")
        if isinstance(series, list):
            lengths.append(len(series))
    return min(lengths) if lengths else 0


def screen_answer(
    answer: dict[str, Any],
    *,
    tool_results: Any = None,
    evidence_ids: set[str] | None = None,
    # Set by callers whose metrics carry a time series. A movement claimed
    # without two points to move between is rejected rather than believed.
    require_series: bool = False,
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
        for value in numbers_a_tool_supplied(tool_results or []):
            refusal_numbers |= rounded_forms(value)

        # A refusal that quotes a figure the tools produced is an answer filed
        # under the wrong heading. Measured on the golden set: "The decision was
        # recorded on 2026-09-06T17:02:55" and "No findings were raised against
        # this case" both arrived as `NO_EVIDENCE` refusals, which is the answer
        # the officer asked for under a label telling them to ignore it. The
        # earlier rule caught only numbers the tools did not produce, so a
        # correct fact in the wrong field went through.
        stated = _numbers_in(why) & refusal_numbers
        if stated:
            screening.rejections.append(
                {
                    "where": "refusal.reason",
                    "reason": (
                        "this is an answer, not a refusal: it states what the tools "
                        "returned, so put it in `answer` and cite it"
                    ),
                    "detail": sorted(stated)[:5],
                }
            )

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

    if require_series and _TREND.search(text) and _series_points(tool_results) < 2:
        match = _TREND.search(text)
        screening.rejections.append(
            {
                "where": "answer",
                "reason": (
                    "claims a movement over time, and no metric in this run returned "
                    "two periods to move between"
                ),
                "detail": [match.group(0) if match else ""],
            }
        )

    known_numbers: set[str] = set()
    for value in numbers_a_tool_supplied(tool_results or []):
        known_numbers |= rounded_forms(value)
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

    # Same distinction as `screen_opinion`: None is a caller that cannot check,
    # an empty set is a run that produced nothing to cite.
    available = evidence_ids if evidence_ids is not None else set()
    if evidence_ids is not None:
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
