"""The member assistant (T-071, docs/06 §2.3, docs/09 §5).

A member talks to this. That single fact changes every default: the identity
comes from the token rather than the conversation, the tools reach one person's
record and no further, and the two things that must never happen are decided by
rules before a model is involved.

The order is fixed and it matters:

1. **Distress first.** A member who has lost their job is not asking a
   question. If the classifier sees hardship, a complaint, a bereavement or
   something worse, a person is fetched and the model is never called. An
   assistant that answers "your next payment is on the 3rd" to "I lost my job"
   is technically correct and indefensible.
2. **Then what may not be said.** Whether they will be approved, what their
   score is, anything about anybody else. Refused by rule, in words a member
   can read.
3. **Only then the model**, over tools that can see one member's own record.

Every turn on both sides is written down. When a member says "your app told me
my payment was fine", the answer has to be a row saying what it actually said.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ai.guardrails.hardship import Signal, classify
from ai.guardrails.questions import Refusal, check_member_question
from app.copilot import ANSWER_SCHEMA_ID, CopilotQuestion, CopilotResult, answer_question
from app.gateway_client import GatewayClient

__all__ = ["AssistantReply", "answer_member", "gather_member_tools"]

#: What the assistant reads before answering. Every one of them is scoped to
#: the member in the token by the registry, so this list is the whole reach of
#: the agent: there is nothing it can ask for that is not here.
#: What a member reads when nothing the model produced survived the output
#: screen. The screen's own complaints go to an officer, who can act on them;
#: a member told "the answer could not be grounded in this case file" has been
#: handed a developer's sentence about their own money.
UNGROUNDED_REPLY = (
    "I am sorry, I could not answer that reliably, and I would rather say so "
    "than give you something I am not sure of. A colleague can help: you can "
    "ask me to arrange a callback."
)

MEMBER_TOOLS = (
    "get_my_balance",
    "get_my_next_payment",
    "get_my_application",
    "get_missing_documents",
)


@dataclass
class AssistantReply:
    """One turn: what was said back, and what happened underneath."""

    answer: dict[str, Any]
    grounded: bool
    attempts: int
    signal: str | None = None
    handoff_id: str | None = None
    tools_read: tuple[str, ...] = ()
    tools_unavailable: tuple[str, ...] = ()
    screening: dict[str, Any] | None = None
    latency_ms: float = 0.0

    def as_contract(self) -> dict[str, Any]:
        body = {
            **self.answer,
            "grounded": self.grounded,
            "attempts": self.attempts,
            "tools_read": list(self.tools_read),
            "tools_unavailable": list(self.tools_unavailable),
            "latency_ms": round(self.latency_ms, 1),
        }
        if self.signal:
            body["signal"] = self.signal
        if self.handoff_id:
            body["handoff_id"] = self.handoff_id
        if self.screening is not None:
            body["screening"] = self.screening
        return body


def _said(text: str, *, citations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema": ANSWER_SCHEMA_ID,
        "answer": text,
        "citations": citations or [],
    }


async def gather_member_tools(member_id: str) -> list[dict[str, Any]]:
    """Read this member's own record, and nothing else.

    The tools are called here rather than chosen by the model, for the same
    reason the officer copilot's are: an agent that picks what to read will
    eventually pick somebody else. The registry pins `member_id` to the scope
    below, so even a tool call that named another member would be refused
    before it reached a service.
    """
    from ai.tools import registry
    from cio_tools.grants import Grant, GrantRegistry
    from cio_tools.registry import ToolContext
    from cio_tools.spec import PermittedUse

    scoped = registry.with_grants(
        GrantRegistry([Grant(agent_id="member_assistant", tool=name, max_calls=1) for name in MEMBER_TOOLS])
    )
    context = ToolContext(
        agent_id="member_assistant",
        run_id=member_id,
        purpose=PermittedUse.SERVICING,
        principal=member_id,
        member_id=member_id,
    )

    gathered: list[dict[str, Any]] = []
    for name in MEMBER_TOOLS:
        try:
            result = await scoped.call(name, {"member_id": member_id}, context)
        except Exception as exc:
            # Named rather than dropped. "I cannot see your application at the
            # moment" is a reply a member can act on; answering as though the
            # application did not exist is not.
            gathered.append({"tool": name, "unavailable": str(exc)})
            continue
        gathered.append({"tool": name, "result": result, "evidence_refs": []})
    return gathered


async def raise_handoff(
    member_id: str, signal: Signal, *, said: str, case_id: str | None = None
) -> str | None:
    """Put a person between the member and this assistant.

    Returns the handoff id, or None if the request could not be recorded. The
    caller must check: telling somebody in difficulty that a colleague will
    call, when no row exists to make that true, is the worst failure this
    module has.
    """
    from ai.tools import registry
    from cio_tools.grants import Grant, GrantRegistry
    from cio_tools.registry import ToolContext
    from cio_tools.spec import PermittedUse

    scoped = registry.with_grants(
        GrantRegistry([Grant(agent_id="member_assistant", tool="request_callback", max_calls=1)])
    )
    context = ToolContext(
        agent_id="member_assistant",
        run_id=member_id,
        purpose=PermittedUse.SERVICING,
        principal=member_id,
        member_id=member_id,
    )
    try:
        result = await scoped.call(
            "request_callback",
            {
                "member_id": member_id,
                "reason": signal.reason,
                "urgency": signal.urgency,
                "signal": signal.signal,
                "said": said,
            },
            context,
        )
    except Exception:
        return None
    return str(result.get("handoff_id")) if isinstance(result, dict) else None


async def answer_member(
    *,
    member_id: str,
    question: str,
    bundle: Any,
    client: GatewayClient,
    output_schema: dict[str, Any],
    case_id: str | None = None,
    run_id: str = "",
    budget_tokens: int = 0,
) -> AssistantReply:
    """One turn of a member conversation."""
    import time

    started = time.perf_counter()

    signal = classify(question)
    if signal is not None:
        handoff_id = await raise_handoff(member_id, signal, said=question, case_id=case_id)
        if handoff_id is None:
            # The promise could not be made, so it is not made. A member is
            # given the number instead of a reassurance nothing is behind.
            return AssistantReply(
                answer=_said(
                    "Thank you for telling me. I could not raise this with a colleague "
                    "just now, so please call us on the number on your statement and "
                    "somebody will help you."
                ),
                grounded=True,
                attempts=0,
                signal=signal.signal,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        return AssistantReply(
            answer=_said(signal.reply),
            grounded=True,
            attempts=0,
            signal=signal.signal,
            handoff_id=handoff_id,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    tool_results = await gather_member_tools(member_id)
    result: CopilotResult = await answer_question(
        CopilotQuestion(
            agent_id=bundle.agent_id,
            question=question,
            case_id=case_id or "",
            member_id=member_id,
            tool_results=tool_results,
            run_id=run_id or f"{member_id}:{bundle.agent_id}",
            budget_tokens=budget_tokens,
        ),
        bundle=bundle,
        client=client,
        output_schema=output_schema,
        guard=_member_guard(member_id),
        fallback_reason=UNGROUNDED_REPLY,
    )

    return AssistantReply(
        answer=result.answer,
        grounded=result.grounded,
        attempts=result.attempts,
        tools_read=tuple(entry["tool"] for entry in tool_results if "result" in entry),
        tools_unavailable=tuple(entry["tool"] for entry in tool_results if "unavailable" in entry),
        screening=result.screening,
        latency_ms=(time.perf_counter() - started) * 1000,
    )


def _member_guard(member_id: str) -> Callable[[str], Refusal | None]:
    def guard(text: str) -> Refusal | None:
        return check_member_question(text, member_id=member_id)

    return guard
