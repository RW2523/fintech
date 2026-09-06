"""Questions a copilot must refuse, decided before a model sees them.

The officer copilot was measured answering all five questions it must refuse,
including "what is this member's ethnicity" and "what is the weather in the
capital today", while grounding every one of them in the case file. It was not
lying: it had read the case, it cited real evidence, and it answered a question
nobody asked. Groundedness does not make an answer appropriate.

So the refusals that matter are decided here, by rules, before the model is
called. A refusal that depends on a model's mood is not a control. What the
model is for is the questions it should answer.

These rules are deliberately narrow. A rule that refused too much would push
officers back to reading the file by hand, which is the outcome this panel
exists to avoid, so each pattern is one that is wrong in every context rather
than merely suspicious.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = ["Refusal", "check_member_question", "check_question"]


@dataclass(frozen=True, slots=True)
class Refusal:
    code: str
    reason: str

    def as_answer(self) -> dict[str, Any]:
        return {
            "schema": "copilot_answer/1.0",
            "answer": "",
            "citations": [],
            "refusal": {"reason": self.reason, "code": self.code},
        }


#: Protected characteristics. None is collected by any service in the platform,
#: so a question turning on one cannot be answered from the file even in
#: principle, and answering "there is no record of that" invites the follow-up.
_PROTECTED = re.compile(
    r"\b(ethnic\w*|race|racial|religio\w*|faith|gender|sex|sexual\w*|"
    r"disab\w*|health|medical|illness|pregnan\w*|marital|political|"
    r"party|union|caste|tribe|nationality|immigration)\b",
    re.IGNORECASE,
)

#: Asking the platform to decide, rather than asking what it decided. The
#: distinction is the whole relationship: an officer may read the
#: recommendation and must not be handed one by an assistant with no
#: accountability.
#: Written as two halves rather than one alternation of phrasings: "will this
#: application be approved" did not match the first version, which listed the
#: subjects it expected between "will" and the verb. Asking about the outcome
#: is the thing being caught, and the words in between do not matter.
_OUTCOME = r"(approv\w*|declin\w*|reject\w*|accept\w*|turn(ed)? down)"
_PREDICTS = re.compile(
    rf"\bwill\b[^?]{{0,60}}\b{_OUTCOME}"
    rf"|\bshould i\b[^?]{{0,40}}\b{_OUTCOME}"
    rf"|\bwould you\b[^?]{{0,40}}\b{_OUTCOME}"
    rf"|\bdo you (think|recommend|reckon)\b"
    rf"|\bwhat should i do\b"
    rf"|\b(likely|going|expected) to be\b[^?]{{0,30}}\b{_OUTCOME}"
    rf"|\bis (this|it) (a )?(good|bad) (idea|risk|bet)\b",
    re.IGNORECASE,
)

#: A different case or member than the one in scope.
_CASE_ID = re.compile(r"\b(case|member|snap|dr)_[0-9A-Za-z-]{4,}\b", re.IGNORECASE)
_MEMBER_ID = re.compile(r"\bM-\d{4,}\b")

#: Plainly not credit work. Short and literal on purpose: a broad topic filter
#: would refuse a legitimate question that happened to use an ordinary word.
_OUT_OF_SCOPE = re.compile(
    r"\b(weather|football|recipe|joke|poem|song|holiday|film|movie|"
    r"who won|capital of|translate this|write me a)\b",
    re.IGNORECASE,
)


def check_question(question: str, *, case_id: str, member_id: str | None = None) -> Refusal | None:
    """The refusal this question earns, or None if a model should answer it.

    Ordered so the most specific wins: a question naming another member's
    ethnicity is refused for naming another member, because that is the fact
    the officer most needs to hear.
    """
    text = question.strip()

    for match in _CASE_ID.finditer(text):
        named = match.group(0)
        if named.lower() != case_id.lower():
            return Refusal(
                "ANOTHER_CASE",
                f"I can only read {case_id}. Open {named} to ask about it.",
            )

    for match in _MEMBER_ID.finditer(text):
        if member_id is None or match.group(0).upper() != member_id.upper():
            return Refusal(
                "ANOTHER_CASE",
                f"I can only read the case in front of you, not {match.group(0)}.",
            )

    if _PROTECTED.search(text):
        return Refusal(
            "PROTECTED_CHARACTERISTIC",
            "No service in this platform collects that, and it is not an input to any "
            "decision. I cannot answer questions that turn on it.",
        )

    if _PREDICTS.search(text):
        return Refusal(
            "WOULD_PREDICT_DECISION",
            "I do not predict or recommend outcomes. The platform's recommendation is "
            "on the decision card, and the decision is yours.",
        )

    if _OUT_OF_SCOPE.search(text):
        return Refusal(
            "OUT_OF_SCOPE",
            "I only answer questions about the case file in front of you.",
        )

    return None


# ---------------------------------------------------------------------------
# the member assistant (T-071)
# ---------------------------------------------------------------------------
#: What a member may not be told, whatever the file says. The officer rules
#: above are about scope; these are about role. A member asking "will I be
#: approved" is asking a reasonable question of the wrong party, and the honest
#: answer names the right one rather than guessing on their behalf.
_MEMBER_OUTCOME = re.compile(
    rf"\b(will|would|am i|are you going to|going to)\b[^?]{{0,60}}\b{_OUTCOME}"
    rf"|\b{_OUTCOME}\b[^?]{{0,40}}\b(my|me)\b"
    rf"|\bdo i qualify\b|\bam i eligible\b|\bwhat are my chances\b"
    rf"|\bhow likely\b|\bwhat score\b|\bmy (credit )?score\b"
    rf"|\bwhy was i (declined|rejected|turned down)\b"
    rf"|\bwhat do you think\b",
    re.IGNORECASE,
)

#: A member asking about somebody else. The tools cannot reach another member,
#: so this never leaks; it is refused early so the refusal is a sentence rather
#: than an empty answer with no explanation.
_ANOTHER_MEMBER = re.compile(
    r"\b(my (neighbour|friend|colleague|brother|sister|cousin)'?s? (account|balance|"
    r"application|loan))\b|\bsomeone else'?s\b|\banother member\b",
    re.IGNORECASE,
)


def check_member_question(question: str, *, member_id: str | None = None) -> Refusal | None:
    """The refusal a member's question earns, or None.

    Separate from `check_question` because the two agents refuse different
    things for different reasons. An officer may read the recommendation; a
    member may not be handed one by a chat window. An officer may ask about
    another case by opening it; a member may not ask about another member at
    all.
    """
    text = question.strip()

    if _ANOTHER_MEMBER.search(text):
        return Refusal(
            "ANOTHER_CASE",
            "I can only see your own records. If you are asking on behalf of "
            "somebody else, they will need to contact us themselves.",
        )

    for match in _MEMBER_ID.finditer(text):
        if member_id is None or match.group(0).upper() != member_id.upper():
            return Refusal(
                "ANOTHER_CASE",
                "I can only see your own records.",
            )

    if _PROTECTED.search(text):
        return Refusal(
            "PROTECTED_CHARACTERISTIC",
            "We do not hold anything like that about you, and nothing like it "
            "is used in any decision about your account.",
        )

    if _MEMBER_OUTCOME.search(text):
        return Refusal(
            "WOULD_PREDICT_DECISION",
            "I cannot tell you what the outcome will be, and I would rather say "
            "so than guess. Your application is assessed against our published "
            "lending policy and a person makes the decision. We will write to "
            "you as soon as it is made, and I can tell you what stage it is at "
            "or what we are still waiting for.",
        )

    if _OUT_OF_SCOPE.search(text):
        return Refusal(
            "OUT_OF_SCOPE",
            "I can only help with your accounts and your application here.",
        )

    return None
