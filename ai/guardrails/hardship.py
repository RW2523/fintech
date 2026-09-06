"""Things a member may say that no assistant should answer alone (T-071).

A member writing "I lost my job last week" is not asking a question. Treating
it as one and replying with their next payment date is the single worst thing
this platform could do, and it is exactly what a helpful, well-grounded, tool-
using assistant will do if nothing stops it first.

So the classification happens here, deterministically, before the model is
called. Four signals get a person: hardship, complaint, bereavement and
vulnerability. Each raises a handoff and each is emitted as
`member.hardship_signal.v1`, because the cooperative wants to know about them
whether or not the member asked for anything.

The rules err towards handing off. A false positive costs a person a phone
call to somebody who was fine. A false negative is a member in trouble being
told their balance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["SIGNALS", "Signal", "classify"]

#: The four signals, in the order they are tested. Order matters: a member who
#: has been bereaved and also cannot pay is a bereavement first, because that
#: is what the person picking it up needs to see at the top.
SIGNALS = ("BEREAVEMENT", "COMPLAINT", "HARDSHIP", "VULNERABILITY")


@dataclass(frozen=True, slots=True)
class Signal:
    """A reason to stop and fetch a person."""

    signal: str
    urgency: str
    reason: str
    #: What the assistant says while the handoff is being raised. Fixed text,
    #: not generated: this is the one moment in the conversation where a
    #: model's improvisation has the most to lose and the least to add.
    reply: str


_BEREAVEMENT = re.compile(
    r"\b(passed away|passing away|died|dying|death|deceased|funeral|"
    r"bereave\w*|widow\w*|my (husband|wife|father|mother|son|daughter|"
    r"partner|dad|mum|mom) (has )?(passed|died))\b",
    re.IGNORECASE,
)

_COMPLAINT = re.compile(
    r"\b(complain\w*|ombudsman|regulator|fca|financial conduct|"
    r"legal action|sue you|my (solicitor|lawyer)|"
    r"unfair\w*|mis-?sold|misled|discriminat\w*|"
    r"unacceptable|disgrace\w*|appalling)\b",
    re.IGNORECASE,
)

#: Money trouble, said the way people actually say it. "Struggling" and "cannot
#: afford" carry this far more often than the word "hardship", which members
#: almost never use about themselves.
_HARDSHIP = re.compile(
    r"\b(lost my job|lost her job|lost his job|made redundant|redundanc\w*|"
    r"laid off|let go from|out of work|unemploy\w*|no income|"
    r"can(no|')?t afford|cannot afford|can(no|')?t pay|cannot pay|"
    r"unable to pay|struggl\w*|behind on|falling behind|fell behind|"
    r"in arrears|no money|broke|evict\w*|repossess\w*|bailiff\w*|"
    r"reduce(d)? (my )?hours|hours (were |been )?cut|pay ?cut|"
    r"food bank|benefits stopped|universal credit|"
    r"debt (management|charity|advice)|payday loan|"
    r"defer|payment holiday|freeze my payments|hardship)\b",
    re.IGNORECASE,
)

#: Distress and vulnerability. Deliberately last and deliberately short: these
#: patterns are the ones where a wrong answer is least recoverable, and a long
#: list of near-synonyms would fire on ordinary frustration.
_VULNERABILITY = re.compile(
    r"\b(suicid\w*|kill myself|end my life|self.?harm|"
    r"can(no|')?t (go on|cope)|cannot cope|breakdown|"
    r"domestic (abuse|violence)|coerc\w*|"
    r"terminal\w*|hospice|carer for|dementia|"
    r"homeless\w*|sleeping rough)\b",
    re.IGNORECASE,
)

_RULES: tuple[tuple[str, re.Pattern[str], str, str, str], ...] = (
    (
        "BEREAVEMENT",
        _BEREAVEMENT,
        "PRIORITY",
        "the member wrote about a death",
        "I am very sorry. I am not the right person for this, so I have asked a "
        "colleague to contact you. There is nothing you need to do now, and "
        "nothing on your account will be chased while we speak to you.",
    ),
    (
        "VULNERABILITY",
        _VULNERABILITY,
        "PRIORITY",
        "the member wrote something that needs a person now",
        "Thank you for telling me. I have asked a colleague to contact you, and "
        "they will be in touch as a priority. If you need help before then, "
        "please call us on the number on your statement.",
    ),
    (
        "COMPLAINT",
        _COMPLAINT,
        "PRIORITY",
        "the member is making a complaint",
        "I am sorry. I am not able to handle a complaint, so I have passed this "
        "to a colleague who can, and they will contact you. Your complaint is "
        "recorded from today.",
    ),
    (
        "HARDSHIP",
        _HARDSHIP,
        "PRIORITY",
        "the member described difficulty paying",
        "Thank you for telling me. I have asked a colleague to contact you so "
        "we can look at your options together. Please do not worry about "
        "answering anything else in the meantime.",
    ),
)


def classify(said: str) -> Signal | None:
    """The signal in what a member wrote, or nothing.

    Tested in the order of `_RULES`, which is not the order of `SIGNALS`:
    bereavement and vulnerability outrank a complaint, and a complaint outranks
    hardship, because the reply and the queue differ and the most serious one
    has to win.
    """
    text = said or ""
    for signal, pattern, urgency, reason, reply in _RULES:
        if pattern.search(text):
            return Signal(signal=signal, urgency=urgency, reason=reason, reply=reply)
    return None
