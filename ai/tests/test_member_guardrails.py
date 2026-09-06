"""T-071, T-072 — what a member may say, and what a copilot may be asked.

Three rule sets, all deterministic, all running before a model is called.

The hardship classifier decides whether a member's message is a question at
all. The member refusals decide whether the answer is one an assistant may
give. The manager refusals decide whether a question about the book is really a
question about somebody in it.

None of them depends on a model's judgement, because a control that depends on
a model's mood is not a control.
"""

from __future__ import annotations

import pytest

from ai.guardrails.hardship import SIGNALS, classify
from ai.guardrails.questions import check_manager_question, check_member_question

MEMBER = "M-000123"


# ---------------------------------------------------------------------------
# the hardship classifier
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "said,expected",
    [
        ("I lost my job last week", "HARDSHIP"),
        ("I was made redundant on Friday", "HARDSHIP"),
        ("I am struggling to pay this month", "HARDSHIP"),
        ("I cannot afford the instalment", "HARDSHIP"),
        ("my hours were cut and I have no money", "HARDSHIP"),
        ("can I have a payment holiday", "HARDSHIP"),
        ("my father passed away last month", "BEREAVEMENT"),
        ("my wife died in June", "BEREAVEMENT"),
        ("I want to make a complaint about this", "COMPLAINT"),
        ("this is completely unfair and I am going to the ombudsman", "COMPLAINT"),
        ("I cannot cope any more", "VULNERABILITY"),
        ("I am homeless at the moment", "VULNERABILITY"),
    ],
)
def test_distress_is_recognised(said: str, expected: str) -> None:
    signal = classify(said)
    assert signal is not None, f"not classified: {said!r}"
    assert signal.signal == expected


@pytest.mark.parametrize(
    "said",
    [
        "what is my balance",
        "when is my next payment due",
        "how is my application going",
        "what documents do you still need",
        "what is the profit rate on the standard product",
        "can I pay early",
    ],
)
def test_an_ordinary_question_is_left_alone(said: str) -> None:
    """A classifier that fires on everything sends every member to a queue.

    False positives are cheap and false negatives are not, but a rule that
    caught "can I pay early" would make the assistant useless and the handoff
    queue meaningless, which costs the members in real difficulty their place
    in it.
    """
    assert classify(said) is None


def test_the_gravest_signal_wins() -> None:
    """A bereaved member who also cannot pay is a bereavement.

    Both are true and they go to different people. The one at the top of the
    row is what the person picking it up reads first.
    """
    signal = classify("my mother died and now I cannot pay the instalment")
    assert signal is not None
    assert signal.signal == "BEREAVEMENT"


def test_every_signal_carries_a_reply_that_promises_a_person() -> None:
    """The reply is fixed text, and this is what it must contain.

    A generated reply to a bereavement is a risk with nothing to gain, so each
    one is written down. This asserts the promise is actually in them.
    """
    for said in ("I lost my job", "my father died", "I want to complain", "I cannot cope"):
        signal = classify(said)
        assert signal is not None
        assert signal.signal in SIGNALS
        assert "colleague" in signal.reply
        assert signal.urgency == "PRIORITY"


def test_a_reply_never_asks_a_member_in_difficulty_to_do_anything() -> None:
    for said in ("I lost my job", "my father died", "I cannot cope"):
        signal = classify(said)
        assert signal is not None
        assert "upload" not in signal.reply.lower()
        assert "send us" not in signal.reply.lower()


# ---------------------------------------------------------------------------
# what a member may be told
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "question,code",
    [
        ("Will I be approved?", "WOULD_PREDICT_DECISION"),
        ("Will my application be approved?", "WOULD_PREDICT_DECISION"),
        ("Am I going to be declined?", "WOULD_PREDICT_DECISION"),
        ("Do I qualify for a bigger loan?", "WOULD_PREDICT_DECISION"),
        ("What are my chances?", "WOULD_PREDICT_DECISION"),
        ("What is my credit score?", "WOULD_PREDICT_DECISION"),
        ("What do you think?", "WOULD_PREDICT_DECISION"),
        ("What is my neighbour's balance?", "ANOTHER_CASE"),
        ("Can you check M-000999 for me?", "ANOTHER_CASE"),
        ("What is my ethnicity on file?", "PROTECTED_CHARACTERISTIC"),
        ("What is the weather today?", "OUT_OF_SCOPE"),
    ],
)
def test_a_member_is_not_told_these(question: str, code: str) -> None:
    refusal = check_member_question(question, member_id=MEMBER)
    assert refusal is not None, f"not refused: {question!r}"
    assert refusal.code == code


@pytest.mark.parametrize(
    "question",
    [
        "What is my balance?",
        "When is my next payment due?",
        "How is my application going?",
        "What documents do you still need from me?",
        "What is the maximum I could borrow on the standard product?",
        "Am I up to date?",
    ],
)
def test_an_ordinary_member_question_reaches_the_model(question: str) -> None:
    """A refusal rule that fires here has taken the service away.

    "What is the maximum I could borrow" is a question about published product
    terms, not about this member's chances, and refusing it would leave the
    assistant unable to answer the thing a product page already says.
    """
    assert check_member_question(question, member_id=MEMBER) is None


def test_a_member_may_ask_about_themselves_by_id() -> None:
    assert check_member_question(f"is {MEMBER} up to date?", member_id=MEMBER) is None


def test_a_refusal_tells_the_member_what_happens_instead() -> None:
    """A refusal that only says no leaves somebody stuck.

    The member is not the officer: they cannot open the file, so the refusal
    has to carry the next step or it is a door with nothing behind it.
    """
    refusal = check_member_question("Will I be approved?", member_id=MEMBER)
    assert refusal is not None
    assert "policy" in refusal.reason
    assert "write to you" in refusal.reason


# ---------------------------------------------------------------------------
# the manager copilot (T-072)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "question,code",
    [
        ("Tell me about member M-000042", "ANOTHER_CASE"),
        ("Which members are in CRITICAL? Name them.", "ANOTHER_CASE"),
        ("Show me the cases that were overridden", "ANOTHER_CASE"),
        ("Who is the applicant on case_01ARZ3NDEKTSV4RRFFQ69G5FAW?", "ANOTHER_CASE"),
        ("What will delinquency be next quarter?", "WOULD_PREDICT_DECISION"),
        ("Forecast approvals for December", "WOULD_PREDICT_DECISION"),
        ("Break the approval rate down by ethnicity", "PROTECTED_CHARACTERISTIC"),
        ("What is the weather in the capital?", "OUT_OF_SCOPE"),
    ],
)
def test_a_manager_is_not_answered_these(question: str, code: str) -> None:
    refusal = check_manager_question(question)
    assert refusal is not None, f"not refused: {question!r}"
    assert refusal.code == code


@pytest.mark.parametrize(
    "question",
    [
        "What is the autonomous share?",
        "How many cases are still waiting for a person?",
        "List the branches by approval rate",
        "How many members are in each early-warning state?",
        "Why did approvals fall this quarter?",
        "What is the approval rate by product?",
        "How is the book performing on delinquency?",
        "What is the override rate?",
    ],
)
def test_a_manager_question_about_the_book_reaches_the_model(question: str) -> None:
    """A refusal rule that fires here has taken the cockpit away.

    "List the branches by approval rate" is exactly what a management cockpit
    is for. An earlier pattern refused it, because it matched "list the"
    without asking what was being listed, and a branch is a dimension rather
    than a person.
    """
    assert check_manager_question(question) is None
