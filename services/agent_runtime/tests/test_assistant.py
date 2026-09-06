"""T-071 — the member assistant (docs/06 §2.3, docs/09 §5).

What is tested here is the order of the pipeline, because the order is the
safety property. Distress is classified before anything else and short-circuits
the model entirely. Then the refusals. Only then a model, over tools that reach
one member's own record.

A test that only checked the final answer would pass with the stages in any
order, and the wrong order is an assistant that answers "your next payment is
on the 3rd" to "I lost my job".
"""

from __future__ import annotations

from typing import Any

import pytest

from app.assistant import UNGROUNDED_REPLY, AssistantReply, answer_member

MEMBER = "M-000123"


class Bundle:
    """The member assistant's bundle, without reading it off disk."""

    agent_id = "member_assistant"
    agent_version = "0000000000000000"
    route = "agent"
    prompt = "answer the member"
    max_output_tokens = 900
    temperature = 0.0


SCHEMA: dict[str, Any] = {"type": "object"}

#: The evidence ref matters. `get_my_balance` declares an `EvidenceSpec`, so a
#: real call returns one, and a fixture without it exercises a shape the tool
#: never produces: with nothing citable in the run, the output screen now
#: refuses any citation at all, which is the point of it.
EVIDENCE = "ev_01ARZ3NDEKTSV4RRFFQ69G5FAW"

BALANCE = {
    "tool": "get_my_balance",
    "result": {
        "member_id": MEMBER,
        "accounts": [{"account_id": "A-000061", "instalments_paid": 11, "instalments_total": 11}],
        "savings_balance": "1364.87",
        "savings_as_of": "2026-08-31",
    },
    "evidence_refs": [{"evidence_id": EVIDENCE}],
}


class Gateway:
    """A gateway that records whether it was called at all.

    The assertion that matters most in this file is a negative one: on a
    hardship message the model must never be reached, and the only way to test
    that is to ask the gateway afterwards.
    """

    def __init__(self, answer: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.answer = answer or {
            "schema": "copilot_answer/1.0",
            "answer": "Your savings balance is 1364.87 as of 2026-08-31.",
            "citations": [{"ref": EVIDENCE, "what": "your account"}],
        }

    async def complete(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        class Completion:
            value = self.answer

        return Completion()


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    gathered = [BALANCE]

    async def fake_gather(member_id: str) -> list[dict[str, Any]]:
        assert member_id == MEMBER
        return gathered

    monkeypatch.setattr("app.assistant.gather_member_tools", fake_gather)
    return gathered


@pytest.fixture
def handoffs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    raised: list[dict[str, Any]] = []

    async def fake_handoff(member_id: str, signal: Any, *, said: str, case_id: str | None = None) -> str:
        raised.append({"member_id": member_id, "signal": signal.signal, "said": said})
        return "hnd_01ARZ3NDEKTSV4RRFFQ69G5FAW"

    monkeypatch.setattr("app.assistant.raise_handoff", fake_handoff)
    return raised


async def ask(question: str, *, gateway: Gateway | None = None) -> AssistantReply:
    return await answer_member(
        member_id=MEMBER,
        question=question,
        bundle=Bundle(),
        client=gateway or Gateway(),
        output_schema=SCHEMA,
    )


# ---------------------------------------------------------------------------
# distress comes first
# ---------------------------------------------------------------------------
async def test_a_job_loss_never_reaches_the_model(
    tools: list[dict[str, Any]], handoffs: list[dict[str, Any]]
) -> None:
    gateway = Gateway()
    reply = await ask("I lost my job last week", gateway=gateway)

    assert reply.signal == "HARDSHIP"
    assert reply.handoff_id
    assert gateway.calls == [], "the model was called on a hardship disclosure"


async def test_the_handoff_carries_what_the_member_actually_wrote(
    tools: list[dict[str, Any]], handoffs: list[dict[str, Any]]
) -> None:
    """A queue of rows all saying "difficulty paying" cannot be triaged.

    Somebody picking one up needs to tell a missed instalment from an eviction
    notice, and the paraphrase is what loses the difference.
    """
    await ask("I lost my job last week and the bailiffs are coming")

    assert handoffs[0]["said"] == "I lost my job last week and the bailiffs are coming"


async def test_a_reply_a_member_reads_when_the_handoff_could_not_be_raised(
    tools: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The promise is not made when nothing is behind it.

    Telling somebody in difficulty that a colleague will call, with no row to
    make it true, is the worst thing in this module.
    """

    async def failed(member_id: str, signal: Any, *, said: str, case_id: str | None = None) -> None:
        return None

    monkeypatch.setattr("app.assistant.raise_handoff", failed)
    reply = await ask("I cannot afford to pay this month")

    assert reply.handoff_id is None
    assert reply.signal == "HARDSHIP"
    assert "number on your statement" in reply.answer["answer"]
    assert "colleague will" not in reply.answer["answer"]


# ---------------------------------------------------------------------------
# then what may not be said
# ---------------------------------------------------------------------------
async def test_an_outcome_question_never_reaches_the_model(tools: list[dict[str, Any]]) -> None:
    gateway = Gateway()
    reply = await ask("Will my application be approved?", gateway=gateway)

    assert reply.answer["refusal"]["code"] == "WOULD_PREDICT_DECISION"
    assert gateway.calls == []


async def test_a_question_about_somebody_else_never_reaches_the_model(
    tools: list[dict[str, Any]],
) -> None:
    gateway = Gateway()
    reply = await ask("What is my neighbour's balance?", gateway=gateway)

    assert reply.answer["refusal"]["code"] == "ANOTHER_CASE"
    assert gateway.calls == []


# ---------------------------------------------------------------------------
# then the model
# ---------------------------------------------------------------------------
async def test_an_ordinary_question_is_answered_from_the_tools(tools: list[dict[str, Any]]) -> None:
    gateway = Gateway()
    reply = await ask("What is my balance?", gateway=gateway)

    assert reply.grounded
    assert reply.signal is None
    assert "1364.87" in reply.answer["answer"]
    assert reply.tools_read == ("get_my_balance",)
    assert len(gateway.calls) == 1


async def test_an_ungrounded_answer_is_not_shown_to_a_member(tools: list[dict[str, Any]]) -> None:
    """The screen's complaints go to an officer, who can act on them.

    A member told "the answer could not be grounded in this case file" has been
    handed a developer's sentence about their own money.
    """
    gateway = Gateway(
        answer={
            "schema": "copilot_answer/1.0",
            "answer": "Your outstanding balance is 9,412.55.",
            "citations": [{"ref": EVIDENCE, "what": "your account"}],
        }
    )
    reply = await ask("What is my balance?", gateway=gateway)

    assert not reply.grounded
    assert reply.answer["refusal"]["reason"] == UNGROUNDED_REPLY
    assert "grounded" not in reply.answer["refusal"]["reason"]


# ---------------------------------------------------------------------------
# what a member actually reads (P8)
# ---------------------------------------------------------------------------
async def test_a_member_is_never_shown_a_machine_code(tools: list[dict[str, Any]]) -> None:
    """Measured: a member asking for their balance was shown "NO_EVIDENCE".

    A refusal passes the output screen trivially, because refusing is always
    allowed and never needs evidence, so the model's own wording reached the
    member unchanged. An officer seeing that code knows what it means and can
    open the file; a member has been handed a symbol from inside the machine.
    """
    gateway = Gateway(
        answer={
            "schema": "copilot_answer/1.0",
            "answer": "",
            "citations": [],
            "refusal": {"reason": "NO_EVIDENCE", "code": "NO_EVIDENCE"},
        }
    )
    reply = await ask("What is my balance?", gateway=gateway)

    assert reply.answer["refusal"]["reason"] == UNGROUNDED_REPLY
    # The refusal stands and so does its code. Only the wording changed.
    assert reply.answer["refusal"]["code"] == "NO_EVIDENCE"


async def test_a_refusal_a_member_can_read_is_left_alone(tools: list[dict[str, Any]]) -> None:
    gateway = Gateway(
        answer={
            "schema": "copilot_answer/1.0",
            "answer": "",
            "citations": [],
            "refusal": {
                "reason": "I cannot see your application at the moment. A colleague can check it.",
                "code": "NO_EVIDENCE",
            },
        }
    )
    reply = await ask("How is my application going?", gateway=gateway)

    assert "colleague can check it" in reply.answer["refusal"]["reason"]
