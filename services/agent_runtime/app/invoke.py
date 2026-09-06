"""The invocation loop (docs/06 §1).

Load the bundle, assemble the context, call the gateway with the output
schema, run tools while the agent asks for them, validate what comes back, and
sign it. Every failure has one defined outcome, and the outcome is never an
opinion that looks fine but is not.

The DEGRADED opinion matters more than the happy path. When an agent cannot
produce something valid, the run needs a record saying so -- with stance
NEED_MORE_EVIDENCE and confidence zero -- rather than a gap. A gap looks like
an agent that had nothing to say.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ai.agents.bundle import Bundle
from ai.guardrails.screen import screen_opinion
from app.context import assemble
from app.gateway_client import GatewayClient, GatewayUnavailableError, SchemaRefusedError
from cio_common.hashing import canonical_json, sha256
from cio_common.ids import new_id

__all__ = ["Invocation", "InvocationResult", "degraded_opinion", "invoke"]

#: docs/06 §1 — one corrective retry, then DEGRADED. The gateway already takes
#: one turn inside itself for schema shape; this one is for the runtime's own
#: rules, which the gateway cannot check.
CORRECTIVE_ATTEMPTS = 1

#: docs/03 — the opinion contract this runtime produces.
OPINION_SCHEMA_ID = "agent_opinion/1.3"

STANCES = ("SUPPORT", "LEAN_SUPPORT", "REVIEW", "NEED_MORE_EVIDENCE", "LEAN_OPPOSE", "OPPOSE", "BLOCK")


@dataclass
class Invocation:
    """One request to run an agent."""

    bundle: Bundle
    snapshot: dict[str, Any]
    committee_run_id: str
    round: str = "ASSESS"
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    clauses: list[dict[str, Any]] = field(default_factory=list)
    prior_opinions: list[dict[str, Any]] = field(default_factory=list)
    temporal_context: dict[str, Any] | None = None
    run_id: str = ""
    budget_tokens: int = 0


@dataclass
class InvocationResult:
    """The opinion, and everything about how it was reached."""

    opinion: dict[str, Any]
    degraded: bool
    attempts: int
    latency_ms: float
    injections: list[dict[str, Any]] = field(default_factory=list)
    screening: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    detail: str | None = None

    def as_contract(self) -> dict[str, Any]:
        return {
            "opinion": self.opinion,
            "degraded": self.degraded,
            "attempts": self.attempts,
            "latency_ms": round(self.latency_ms, 2),
            "injections_detected": self.injections,
            "screening": self.screening,
            "usage": self.usage,
            "detail": self.detail,
        }


def _signature(opinion: dict[str, Any]) -> str:
    """A digest over the opinion's content, excluding the signature itself.

    Not a secret-keyed signature: the ledger's hash chain is what makes an
    opinion tamper-evident. This ties the body to its id so a record cannot be
    edited and still match what the runtime produced.
    """
    body = {k: v for k, v in opinion.items() if k != "signature"}
    return "sha256:" + sha256(canonical_json(body))


def _shell(invocation: Invocation) -> dict[str, Any]:
    """The fields every opinion carries, whatever the model said."""
    bundle = invocation.bundle
    return {
        "schema": OPINION_SCHEMA_ID,
        "opinion_id": new_id("op"),
        "committee_run_id": invocation.committee_run_id,
        "snapshot_id": str(invocation.snapshot.get("snapshot_id") or ""),
        "agent_id": bundle.agent_id,
        "agent_version": bundle.agent_version,
        "round": invocation.round,
        "created_at": datetime.now(UTC).isoformat(),
    }


def degraded_opinion(invocation: Invocation, reason: str) -> dict[str, Any]:
    """docs/06 §1 — what an agent that could not answer leaves behind.

    Confidence zero and an unresolved item naming the failure, so the
    synthesizer sees an agent that could not speak rather than an agent that
    had no concerns.
    """
    opinion = {
        **_shell(invocation),
        "stance": "NEED_MORE_EVIDENCE",
        "confidence": 0.0,
        "reason_codes": [],
        "claims": [],
        "contradictions": [],
        "unresolved": [{"question": reason, "blocking": False}],
        "proposed_actions": [],
        "tool_calls": [],
    }
    opinion["signature"] = _signature(opinion)
    return opinion


def _finish(invocation: Invocation, body: dict[str, Any], tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge what the model said into the shell the runtime controls.

    The model never sets its own id, version, round or timestamp: those are
    facts about the run, and an agent that could choose them could claim to be
    a different agent.
    """
    opinion = {
        **_shell(invocation),
        "stance": str(body.get("stance") or "NEED_MORE_EVIDENCE"),
        "confidence": float(body.get("confidence") or 0.0),
        "reason_codes": list(body.get("reason_codes") or []),
        "claims": list(body.get("claims") or []),
        "contradictions": list(body.get("contradictions") or []),
        "unresolved": list(body.get("unresolved") or []),
        "proposed_actions": list(body.get("proposed_actions") or []),
        "tool_calls": tool_calls,
    }
    if factors := body.get("factor_scores"):
        opinion["factor_scores"] = factors
    if changed := body.get("changed_from_prior"):
        opinion["changed_from_prior"] = changed
    opinion["signature"] = _signature(opinion)
    return opinion


def _runtime_rules(opinion: dict[str, Any]) -> list[str]:
    """Rules the schema cannot express (docs/06 §5.1)."""
    problems: list[str] = []
    if opinion["stance"] not in STANCES:
        problems.append(f"stance {opinion['stance']!r} is not one of {', '.join(STANCES)}")
    if not 0.0 <= opinion["confidence"] <= 1.0:
        problems.append("confidence must be between 0 and 1")
    if len(opinion["claims"]) > 8:
        problems.append("at most 8 claims (rule 6)")
    if len(opinion["unresolved"]) > 4:
        problems.append("at most 4 unresolved items (rule 6)")
    for index, claim in enumerate(opinion["claims"]):
        if len(str(claim.get("text") or "")) > 400:
            problems.append(f"claims[{index}] exceeds 400 characters (rule 6)")
    return problems


async def invoke(
    invocation: Invocation,
    *,
    client: GatewayClient,
    output_schema: dict[str, Any],
) -> InvocationResult:
    """Run one agent and return its opinion, degraded if it could not produce one."""
    started = time.perf_counter()
    bundle = invocation.bundle
    context = assemble(
        prompt=bundle.prompt,
        agent_id=bundle.agent_id,
        agent_version=bundle.agent_version,
        snapshot=invocation.snapshot,
        output_schema=output_schema,
        tool_results=invocation.tool_results,
        clauses=invocation.clauses,
        prior_opinions=invocation.prior_opinions,
        temporal_context=invocation.temporal_context,
    )

    injections = [d.as_dict() for d in context.injections]
    messages = list(context.messages)
    usage: dict[str, int] = {"in": 0, "out": 0, "total": 0}
    attempts = 0
    problems: list[str] = []
    screening: dict[str, Any] = {}

    for attempt in range(1, CORRECTIVE_ATTEMPTS + 2):
        attempts = attempt
        try:
            answer = await client.complete(
                route=bundle.route,
                messages=messages,
                json_schema=output_schema,
                max_tokens=bundle.max_output_tokens,
                temperature=bundle.temperature,
                run_id=invocation.run_id,
                budget_tokens=invocation.budget_tokens,
            )
        except SchemaRefusedError as exc:
            problems = list(exc.errors)
            break
        except GatewayUnavailableError as exc:
            # docs/13 §3 — the deterministic path continues and the case routes
            # to a person. An absent opinion is recorded as absent.
            return InvocationResult(
                opinion=degraded_opinion(invocation, f"the model was unavailable: {exc}"),
                degraded=True,
                attempts=attempt,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                injections=injections,
                usage=usage,
                detail="llm gateway unavailable",
            )

        for key in ("in", "out", "total"):
            usage[key] += int(answer.usage.get(key, 0))

        opinion = _finish(invocation, answer.value or {}, answer.tool_calls)
        problems = _runtime_rules(opinion)
        # Everything the runtime put in front of the agent counts as a source
        # for the numbers rule, not the tool results alone. The case summary
        # comes from the frozen snapshot, so an agent quoting a tenure it was
        # shown is reading rather than computing; screening against tools only
        # punished it for using what it was given.
        shown = [*invocation.tool_results, {"case_summary": invocation.snapshot}]
        result = screen_opinion(opinion, tool_results=shown, evidence_ids=context.evidence_ids)
        screening = result.as_dict()
        problems += [f"{r['where']}: {r['detail']}" for r in result.rejections]

        if not problems:
            return InvocationResult(
                opinion=opinion,
                degraded=False,
                attempts=attempt,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                injections=injections,
                screening=screening,
                usage=usage,
            )

        if attempt <= CORRECTIVE_ATTEMPTS:
            messages = [
                *messages,
                {"role": "assistant", "content": canonical_json(answer.value or {}).decode()},
                {
                    "role": "user",
                    "content": "Your answer broke these rules:\n- "
                    + "\n- ".join(problems[:5])
                    + "\nReply with a corrected JSON object only.",
                },
            ]

    # Named, not "agent output invalid". The screen knows exactly which rule
    # broke and a reader of this response should not have to dig through the
    # screening structure to find out: a degraded opinion with no stated reason
    # is a Council member who fell silent and nobody asked why.
    why = "; ".join(problems[:3]) if problems else "agent output invalid"
    return InvocationResult(
        opinion=degraded_opinion(invocation, why),
        degraded=True,
        attempts=attempts,
        latency_ms=(time.perf_counter() - started) * 1000.0,
        injections=injections,
        screening=screening,
        usage=usage,
        detail=why,
    )
