"""Answering an officer's question about a case (docs/06 §2.3, docs/09 §3.5).

A copilot is the same machinery as a Council agent pointed at a different job:
a bundle, a set of tools, guided decoding into a published contract, and the
output screen. What differs is the standard for saying nothing.

A Council agent that cannot form an opinion writes a degraded one and the run
continues. A copilot that cannot ground an answer must refuse, because an
officer reading a confident sentence will act on it, and there is no
Synthesizer downstream to catch a copilot that was wrong.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ai.guardrails.questions import Refusal, check_question
from ai.guardrails.screen import screen_answer
from app.gateway_client import GatewayClient, SchemaRefusedError
from cio_common.errors import LlmUnavailable

__all__ = ["ANSWER_SCHEMA_ID", "CopilotQuestion", "CopilotResult", "answer_question"]

ANSWER_SCHEMA_ID = "copilot_answer/1.0"

#: One correction. A model that produced an ungrounded answer twice is not
#: going to produce a grounded one on the third go, and each attempt is an
#: officer waiting.
CORRECTIVE_ATTEMPTS = 1

#: What an officer is told when nothing survived the screen. It names the
#: screen's own complaints, which is right for somebody who can act on them.
#: A member cannot, and must never read it: callers with a member on the other
#: end pass their own `fallback_reason`.
_FALLBACK_REASON = "the answer could not be grounded in this case file: {problems}"


@dataclass
class CopilotQuestion:
    agent_id: str
    question: str
    case_id: str
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    clauses: list[dict[str, Any]] = field(default_factory=list)
    run_id: str = ""
    budget_tokens: int = 0
    #: Set when the asker is the member themselves rather than an officer. It
    #: goes into the context in place of a case id, because a member assistant
    #: has no case: it has a person.
    member_id: str | None = None


#: What a question has to get past before a model sees it. The officer rules by
#: default; the member assistant passes its own, because the two agents refuse
#: different things. An officer may read the platform's recommendation and a
#: member may not be handed one by a chat window.
Guard = Callable[[str], Refusal | None]


@dataclass
class CopilotResult:
    answer: dict[str, Any]
    grounded: bool
    attempts: int
    screening: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    problems: list[str] = field(default_factory=list)

    def as_contract(self) -> dict[str, Any]:
        return {
            **self.answer,
            "grounded": self.grounded,
            "attempts": self.attempts,
            "screening": self.screening,
            "latency_ms": round(self.latency_ms, 1),
        }


def refusal(reason: str, code: str = "NO_EVIDENCE") -> dict[str, Any]:
    """A refusal in the shape of an answer.

    Built here rather than asked of the model, because the cases that need one
    most are the ones where the model is least reliable: an unavailable
    gateway, an answer that failed the screen twice, a question about another
    case. Those must refuse the same way every time.
    """
    return {
        "schema": ANSWER_SCHEMA_ID,
        "answer": "",
        "citations": [],
        "refusal": {"reason": reason, "code": code},
    }


def evidence_in(tool_results: list[dict[str, Any]]) -> set[str]:
    """Every identifier the tools in this run produced.

    What a citation is allowed to point at. An id the officer could not open
    from this case is a citation that looks like proof and is not.
    """
    import re

    pattern = re.compile(
        r"\b(?:ev|doc|dr|calc|ent|op|act|hd|tok|run|snap|aud|fnd|ext|mr)_"
        r"[0-9A-HJKMNP-TV-Z]{26}\b"
    )
    found = set(pattern.findall(str(tool_results)))
    # Policy clause ids are not platform ids and do not match the pattern, so
    # they are collected separately: an answer citing AFF-01 is citing the rule
    # it read, which is exactly what it should do.
    found |= set(re.findall(r"\b[A-Z]{3}-\d{2}\b", str(tool_results)))
    # A metric's name is its identifier. Without this the manager copilot has
    # nothing it is allowed to cite: `routing` matches no id pattern, so every
    # citation was rejected and every answer collapsed into a bare refusal
    # while all eight metrics sat in front of it.
    for entry in tool_results:
        result = entry.get("result")
        if isinstance(result, dict) and isinstance(result.get("metric"), str):
            found.add(result["metric"])
    return found


async def answer_question(
    question: CopilotQuestion,
    *,
    bundle: Any,
    client: GatewayClient,
    output_schema: dict[str, Any],
    guard: Guard | None = None,
    fallback_reason: str = "",
    require_series: bool = False,
) -> CopilotResult:
    """One question, answered from the case or refused."""
    import time

    from app.context import assemble

    started = time.perf_counter()

    # Decided before the model is called. The copilot was measured answering
    # all five questions it must refuse while grounding every one of them: it
    # had read the case, cited real evidence, and answered a question nobody
    # asked. Groundedness does not make an answer appropriate, and a refusal
    # that depends on a model's mood is not a control.
    check = guard or (lambda text: check_question(text, case_id=question.case_id))
    refused = check(question.question)
    if refused is not None:
        return CopilotResult(
            answer=refused.as_answer(),
            grounded=True,
            attempts=0,
            screening={"passed": True, "rejections": [], "redactions": []},
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    available = evidence_in(question.tool_results)

    context = assemble(
        prompt=bundle.prompt,
        agent_id=bundle.agent_id,
        agent_version=bundle.agent_version,
        snapshot=({"member_id": question.member_id} if question.member_id else {"case_id": question.case_id}),
        question=question.question,
        output_schema=output_schema,
        tool_results=question.tool_results,
        clauses=question.clauses,
    )

    messages = list(context.messages)
    problems: list[str] = []
    screening: dict[str, Any] = {}
    attempts = 0

    for attempt in range(1, CORRECTIVE_ATTEMPTS + 2):
        attempts = attempt
        try:
            completion = await client.complete(
                route=bundle.route,
                messages=messages,
                json_schema=output_schema,
                max_tokens=bundle.max_output_tokens,
                temperature=bundle.temperature,
                run_id=question.run_id or f"{question.case_id}:{bundle.agent_id}",
                budget_tokens=question.budget_tokens,
            )
        except SchemaRefusedError as exc:
            # A malformed answer is exactly what the corrective loop is for.
            # Before this it raised out of the endpoint as a 500, which tells
            # an officer nothing and looks like the platform is broken rather
            # than the model having produced prose where JSON was asked for.
            problems = [f"the answer was not valid JSON: {exc}"]
            if attempt > CORRECTIVE_ATTEMPTS:
                break
            messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "That reply was not a JSON object matching the schema. Reply "
                        "with one JSON object and nothing else."
                    ),
                },
            ]
            continue
        except LlmUnavailable as exc:
            # No answer rather than a guess. An officer told "the assistant is
            # unavailable" reads the file; one told something plausible does
            # not.
            return CopilotResult(
                answer=refusal(f"the assistant could not be reached ({exc})", code="NOT_PERMITTED"),
                grounded=False,
                attempts=attempt,
                problems=[str(exc)],
                latency_ms=(time.perf_counter() - started) * 1000,
            )

        body = dict(completion.value or {})
        body.setdefault("schema", ANSWER_SCHEMA_ID)

        result = screen_answer(
            body,
            tool_results=question.tool_results,
            evidence_ids=available,
            require_series=require_series,
        )
        screening = result.as_dict()
        if result.passed:
            return CopilotResult(
                answer=body,
                grounded=True,
                attempts=attempt,
                screening=screening,
                latency_ms=(time.perf_counter() - started) * 1000,
            )

        problems = [str(rejection.get("reason")) for rejection in result.rejections]
        if attempt > CORRECTIVE_ATTEMPTS:
            break

        messages = [
            *messages,
            {"role": "assistant", "content": str(body)},
            {
                "role": "user",
                "content": (
                    "That answer was refused by the output policy: "
                    + "; ".join(problems)
                    + ". Answer again using only what the tools returned, citing each "
                    "identifier exactly as it appeared, or refuse."
                ),
            },
        ]

    return CopilotResult(
        answer=refusal(fallback_reason or _FALLBACK_REASON.format(problems="; ".join(problems))),
        grounded=False,
        attempts=attempts,
        screening=screening,
        problems=problems,
        latency_ms=(time.perf_counter() - started) * 1000,
    )
