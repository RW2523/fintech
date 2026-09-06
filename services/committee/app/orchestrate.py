"""The committee state machine (docs/06 §8).

CREATED, ASSESS, CHALLENGE, SYNTHESIZE, NARRATE, DONE, with REPAIR and REVISE
between CHALLENGE and SYNTHESIZE at Tier 2. Every transition is recorded, and
the run reaches DONE whether or not the agents did.

The rule that shapes everything here is that the deliberation may fail and the
decision may not. An agent that times out leaves a degraded opinion; a
Challenger that cannot run leaves the run marked; a narrator that cannot be
reached leaves a template. The Synthesizer always runs, because the
recommendation is deterministic and a case with no recommendation is a case
nobody can act on.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.clients import Clients, UpstreamError
from app.narrate import NARRATIVE_SCHEMA, degraded_narrative, narrative_prompt
from app.tiers import AGENT_TIMEOUT_SHARE, TierDecision
from cio_common.ids import derived_id, new_id

__all__ = ["COUNCIL", "STATES", "RunResult", "run_committee"]

#: docs/06 §8 — the states a run passes through.
STATES = ("CREATED", "ASSESS", "CHALLENGE", "REPAIR", "REVISE", "SYNTHESIZE", "NARRATE", "DONE", "FAILED")

#: docs/06 §2.1 — the five agents that assess, and the one that challenges.
COUNCIL = (
    "document_evidence",
    "policy_affordability",
    "credit_risk",
    "fraud_integrity",
    "member_relationship",
)
CHALLENGER = "challenger"

#: Which family each agent owns, so a factor score can be attributed and a
#: REVISE can be targeted at the agent whose ground the gap is on.
OWNS = {
    "policy_affordability": "CAPACITY",
    "credit_risk": "CONDUCT",
    "fraud_integrity": "INTEGRITY",
    "member_relationship": "COMMITMENT",
}

#: docs/06 §8 — Tier 2 may repair and revise at most twice.
MAX_REPAIR_LOOPS = 2


@dataclass
class RunResult:
    """One completed run, however it ended."""

    run_id: str
    snapshot_id: str
    tier: str
    state: str
    rounds: list[str] = field(default_factory=list)
    opinions: list[dict[str, Any]] = field(default_factory=list)
    decision_record: dict[str, Any] = field(default_factory=dict)
    repair_loops: int = 0
    #: One note per tool the orchestrator called during REPAIR, successful or
    #: not, so the record can show what was looked for as well as what was
    #: found.
    repairs: list[dict[str, Any]] = field(default_factory=list)
    #: Bumped each time repair adds evidence, so an opinion can be read
    #: against the evidence that existed when it was written.
    evidence_revision: int = 0
    #: L1 proposals raised by repair: evidence no tool can fetch.
    proposed_actions: list[dict[str, Any]] = field(default_factory=list)
    timed_out: bool = False
    detail: str | None = None
    seconds: float = 0.0
    budgets: dict[str, Any] = field(default_factory=dict)

    @property
    def degraded_agents(self) -> list[str]:
        return [o["opinion"]["agent_id"] for o in self.opinions if o.get("degraded")]

    def as_contract(self) -> dict[str, Any]:
        return {
            "schema": "committee_run/1.0",
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "tier": self.tier,
            "state": self.state,
            "rounds": self.rounds,
            "repair_loops": self.repair_loops,
            "repairs": self.repairs,
            "evidence_revision": self.evidence_revision,
            "proposed_actions": self.proposed_actions,
            "budgets": self.budgets,
            "decision_record_id": self.decision_record.get("decision_record_id"),
            "timed_out": self.timed_out,
            "detail": self.detail,
            "opinions": [o["opinion"]["opinion_id"] for o in self.opinions],
            "degraded_agents": self.degraded_agents,
            "seconds": round(self.seconds, 2),
        }


async def _invoke(
    clients: Clients,
    *,
    agent_id: str,
    run_id: str,
    snapshot: dict[str, Any],
    round_name: str,
    tool_results: list[dict[str, Any]],
    prior: list[dict[str, Any]] | None = None,
    budget_tokens: int = 0,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """One agent, with a deadline and a degraded answer if it misses it."""
    body: dict[str, Any] = {
        "agent_id": agent_id,
        "committee_run_id": run_id,
        "snapshot": snapshot,
        "round": round_name,
        "tool_results": tool_results,
        "run_id": f"{run_id}:{agent_id}:{round_name}",
        "budget_tokens": budget_tokens,
    }
    if prior:
        body["prior_opinions"] = prior
    try:
        return await asyncio.wait_for(clients.invoke_agent(body), timeout=timeout)
    except TimeoutError:
        return _absent(
            agent_id, run_id, snapshot, round_name, f"the agent did not answer within {timeout:.0f}s"
        )
    except UpstreamError as exc:
        return _absent(agent_id, run_id, snapshot, round_name, f"the agent runtime was unavailable: {exc}")


def _absent(
    agent_id: str, run_id: str, snapshot: dict[str, Any], round_name: str, reason: str
) -> dict[str, Any]:
    """An opinion recording that this agent did not give one.

    Written rather than omitted: a gap in the record looks like an agent with
    no concerns, which is the opposite of what happened.
    """
    opinion = {
        "schema": "agent_opinion/1.3",
        "opinion_id": new_id("op"),
        "committee_run_id": run_id,
        "snapshot_id": str(snapshot.get("snapshot_id") or ""),
        "agent_id": agent_id,
        "agent_version": "unavailable",
        "round": round_name,
        "stance": "NEED_MORE_EVIDENCE",
        "confidence": 0.0,
        "reason_codes": [],
        "claims": [],
        "contradictions": [],
        "unresolved": [{"question": reason, "blocking": False}],
        "proposed_actions": [],
        "tool_calls": [],
        "signature": "unsigned",
        "created_at": datetime.now(UTC).isoformat(),
    }
    return {"opinion": opinion, "degraded": True, "attempts": 0, "latency_ms": 0.0, "detail": reason}


def _factor_scores(opinions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The factor scores the agents reported, one per family.

    An agent that degraded contributes nothing: the Synthesizer treats a
    missing factor as missing, which is what it is.
    """
    found: dict[str, dict[str, Any]] = {}
    for entry in opinions:
        opinion = entry["opinion"]
        if entry.get("degraded"):
            continue
        for family, score in (opinion.get("factor_scores") or {}).items():
            found[family] = {"family": family, **score}
    return list(found.values())


def _standing(opinions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The opinion that stands for each Council agent: its most recent one.

    A revised opinion supersedes the one it revised. Handing the Challenger
    both would have it argue against a position the agent has already moved
    from, and handing the Synthesizer both would count one agent twice.
    """
    latest: dict[str, dict[str, Any]] = {}
    for entry in opinions:
        agent = str(entry["opinion"].get("agent_id") or "")
        if agent and agent != CHALLENGER:
            latest[agent] = entry
    return list(latest.values())


def _latest_challenge(opinions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The Challenger's most recent opinion, which is the one that stands."""
    for entry in reversed(opinions):
        if entry["opinion"].get("agent_id") == CHALLENGER:
            return entry
    return None


def _gaps(opinions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the standing Challenger opinion says is still missing.

    Only the latest one: an earlier round's gap that the repair already filled
    is not still open, and repairing it again would loop on a question that
    has been answered.
    """
    entry = _latest_challenge(opinions)
    if entry is None or entry.get("degraded"):
        return []
    return [item for item in entry["opinion"].get("unresolved") or [] if item.get("question")]


def _repairable(opinions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gaps that name a tool the orchestrator can call to close them."""
    return [item for item in _gaps(opinions) if item.get("requested_tool")]


def _requests(opinions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gaps that name evidence but no tool: somebody has to go and get it."""
    return [
        item for item in _gaps(opinions) if item.get("requested_evidence") and not item.get("requested_tool")
    ]


def _document_requests(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """L1 ActionProposals for evidence no tool can produce (docs/06 §8).

    Level 1 is a proposal, never an action: asking a member for a document is
    something a person decides to do, and the record carries the request so
    they can see what the Challenger wanted and why. Blocking is preserved
    from the gap rather than assumed, because whether the case can proceed
    without the document is the Challenger's judgement, not the orchestrator's.
    """
    proposals: list[dict[str, Any]] = []
    for gap in gaps:
        wanted = str(gap.get("requested_evidence") or "")
        proposals.append(
            {
                "schema": "action_proposal/1.0",
                "action_id": derived_id("act", "repair", wanted, str(gap.get("question") or "")),
                "level": "L1",
                "type": "REQUEST_DOCUMENT",
                "parameters": {"evidence": wanted, "blocking": bool(gap.get("blocking"))},
                "rationale": {
                    "text": str(gap.get("question") or "")[:600],
                    "evidence_refs": list(gap.get("evidence_refs") or []),
                },
                "requires": "OFFICER",
                "proposed_by": CHALLENGER,
                "state": "PROPOSED",
            }
        )
    return proposals


def _affected(gaps: list[dict[str, Any]]) -> list[str]:
    """Which agents a gap is on the ground of.

    A gap that names a family goes back to the agent that owns it. A gap that
    names none goes to everybody: the orchestrator cannot tell whose ground it
    is on, and guessing wrong means the revision never reaches the agent whose
    answer would change.
    """
    families = {str(gap.get("family")).upper() for gap in gaps if gap.get("family")}
    if not families:
        return list(COUNCIL)
    owners = [agent for agent, family in OWNS.items() if family in families]
    return owners or list(COUNCIL)


async def _repair(
    clients: Clients,
    *,
    run_id: str,
    snapshot: dict[str, Any],
    gaps: list[dict[str, Any]],
    timeout: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Call the tools the Challenger asked for, as the workflow.

    Returns the new tool results and a note of every call, including the ones
    that failed. A tool that could not be reached is recorded as unreachable
    rather than dropped: the next round must be able to tell "we looked and
    found nothing" from "we never looked".
    """
    results: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []

    for gap in gaps:
        tool = str(gap.get("requested_tool"))
        body = {
            "tool": tool,
            "args": dict(gap.get("requested_args") or {}),
            "committee_run_id": run_id,
            "principal": "workflow",
            "purpose": "UNDERWRITING",
            "case_id": snapshot.get("case_id"),
            "member_id": (snapshot.get("member") or {}).get("member_ref") or snapshot.get("member_id"),
        }
        try:
            answer = await asyncio.wait_for(clients.call_tool(body), timeout=timeout)
        except (TimeoutError, UpstreamError) as exc:
            notes.append({"tool": tool, "ok": False, "detail": str(exc) or "the tool did not answer"})
            continue

        results.append(
            {
                "tool": tool,
                "result": answer.get("result"),
                "evidence_refs": [{"evidence_id": e} for e in answer.get("evidence_ids") or []],
                "call_id": answer.get("call_id"),
                "requested_by": CHALLENGER,
                "round": "REPAIR",
            }
        )
        notes.append({"tool": tool, "ok": True, "call_id": answer.get("call_id")})

    return results, notes


async def run_committee(
    *,
    clients: Clients,
    snapshot: dict[str, Any],
    tier: TierDecision,
    tool_results: dict[str, list[dict[str, Any]]],
    policy_result: dict[str, Any],
    case_type: str = "ORIGINATION",
    run_id: str | None = None,
    model_health: str = "GREEN",
    synthesize_extra: dict[str, Any] | None = None,
) -> RunResult:
    """Drive one case through the state machine."""
    started = time.perf_counter()
    result = RunResult(
        run_id=run_id or new_id("run"),
        snapshot_id=str(snapshot.get("snapshot_id") or ""),
        tier=tier.tier,
        state="CREATED",
        budgets=dict(tier.budget),
    )
    deadline = started + tier.budget["seconds"]
    per_agent = tier.budget["seconds"] * AGENT_TIMEOUT_SHARE
    tokens_each = max(tier.budget["tokens"] // max(len(COUNCIL) + 1, 1), 2000)

    def remaining() -> float:
        return max(0.0, deadline - time.perf_counter())

    # --- ASSESS ------------------------------------------------------------
    # Tier 0 skips deliberation: policy and the models already decided, and a
    # committee that adds nothing is a committee that costs time.
    if tier.tier != "FAST":
        result.state = "ASSESS"
        result.rounds.append("ASSESS")
        answers = await asyncio.gather(
            *[
                _invoke(
                    clients,
                    agent_id=agent,
                    run_id=result.run_id,
                    snapshot=snapshot,
                    round_name="ASSESS",
                    tool_results=tool_results.get(agent, []),
                    budget_tokens=tokens_each,
                    timeout=min(per_agent, remaining() or per_agent),
                )
                for agent in COUNCIL
            ]
        )
        result.opinions.extend(answers)

        # --- CHALLENGE -----------------------------------------------------
        if remaining() > 0:
            result.state = "CHALLENGE"
            result.rounds.append("CHALLENGE")
            challenge = await _invoke(
                clients,
                agent_id=CHALLENGER,
                run_id=result.run_id,
                snapshot=snapshot,
                round_name="CHALLENGE",
                tool_results=tool_results.get(CHALLENGER, []),
                prior=[a["opinion"] for a in answers],
                budget_tokens=tokens_each,
                timeout=min(per_agent, remaining()),
            )
            result.opinions.append(challenge)
        else:
            result.timed_out = True

        # --- REPAIR and REVISE (Tier 2 only) -------------------------------
        # The Challenger names what is missing; the orchestrator goes and gets
        # it, the agents whose ground the gap is on answer again, and the
        # Challenger sees the result. Bounded at two loops, because a third
        # pass has never changed an outcome that the second did not.
        #
        # Evidence the orchestrator has fetched is added to what the agents
        # see. Their original tool results are kept: a revision is the agent
        # reconsidering with more, not with different, evidence.
        repaired: list[dict[str, Any]] = []

        while (
            tier.tier == "EXTENDED"
            and remaining() > 0
            and result.repair_loops < MAX_REPAIR_LOOPS
            and (_repairable(result.opinions) or _requests(result.opinions))
        ):
            result.repair_loops += 1
            callable_gaps = _repairable(result.opinions)
            uncallable_gaps = _requests(result.opinions)

            result.state = "REPAIR"
            result.rounds.append("REPAIR")

            # The orchestrator calls the tool itself, with the workflow as
            # principal: an agent that could fill its own gap would be
            # deciding what counts as evidence about its own case.
            if callable_gaps:
                fetched, notes = await _repair(
                    clients,
                    run_id=result.run_id,
                    snapshot=snapshot,
                    gaps=callable_gaps,
                    timeout=min(per_agent, remaining()),
                )
                repaired.extend(fetched)
                result.repairs.extend(notes)

            # Evidence no tool can produce becomes a proposal for a person.
            result.proposed_actions.extend(_document_requests(uncallable_gaps))

            if repaired:
                result.evidence_revision += 1

            if remaining() <= 0:
                result.timed_out = True
                break

            # --- REVISE ----------------------------------------------------
            affected = _affected(callable_gaps + uncallable_gaps)
            result.state = "REVISE"
            result.rounds.append("REVISE")
            revised = await asyncio.gather(
                *[
                    _invoke(
                        clients,
                        agent_id=agent,
                        run_id=result.run_id,
                        snapshot=snapshot,
                        round_name="REVISE",
                        tool_results=[*tool_results.get(agent, []), *repaired],
                        prior=[a["opinion"] for a in result.opinions],
                        budget_tokens=tokens_each,
                        timeout=min(per_agent, remaining()),
                    )
                    for agent in affected
                ]
            )
            result.opinions.extend(revised)

            if remaining() <= 0:
                result.timed_out = True
                break

            # --- CHALLENGE again -------------------------------------------
            # The loop condition reads the standing Challenger opinion, so
            # without this the same gap would be repaired until the bound.
            # More to the point, a Challenger that never sees the repair
            # cannot withdraw a reservation the repair answered.
            result.state = "CHALLENGE"
            result.rounds.append("CHALLENGE")
            result.opinions.append(
                await _invoke(
                    clients,
                    agent_id=CHALLENGER,
                    run_id=result.run_id,
                    snapshot=snapshot,
                    round_name="CHALLENGE",
                    tool_results=[*tool_results.get(CHALLENGER, []), *repaired],
                    prior=[a["opinion"] for a in _standing(result.opinions)],
                    budget_tokens=tokens_each,
                    timeout=min(per_agent, remaining()),
                )
            )

    if remaining() <= 0:
        result.timed_out = True

    # --- SYNTHESIZE --------------------------------------------------------
    # Always. A deliberation that failed still produces a decision, and the
    # record says the deliberation failed.
    result.state = "SYNTHESIZE"
    result.rounds.append("SYNTHESIZE")
    reservation = _latest_challenge(result.opinions)
    standing = [*_standing(result.opinions), *([reservation] if reservation else [])]
    try:
        record = await clients.synthesize(
            {
                "snapshot_id": result.snapshot_id,
                "committee_run_id": result.run_id,
                "case_type": case_type,
                "tier": tier.tier,
                "product_code": snapshot.get("product_code"),
                "requested_amount": snapshot.get("amount"),
                "policy_result": policy_result,
                "factor_scores": _factor_scores(standing),
                # The opinions that stand, not every opinion written. A revised
                # opinion supersedes the one it revised, and passing both would
                # count one agent twice in the disagreement measure and have it
                # argue with a position it has already left. The full set stays
                # on the run record for anyone reconstructing the deliberation.
                "opinions": [entry["opinion"] for entry in standing],
                "proposed_actions": result.proposed_actions,
                "model_versions": snapshot.get("model_versions") or {},
                "model_health": "RED" if result.timed_out else model_health,
                **(synthesize_extra or {}),
            }
        )
    except UpstreamError as exc:
        result.state = "FAILED"
        result.detail = f"the synthesizer was unavailable: {exc}"
        result.seconds = time.perf_counter() - started
        return result

    result.decision_record = record

    # --- NARRATE -----------------------------------------------------------
    result.state = "NARRATE"
    result.rounds.append("NARRATE")
    try:
        answer = await asyncio.wait_for(
            clients.narrate(
                {
                    "route": "reasoning",
                    "messages": narrative_prompt(record),
                    "json_schema": NARRATIVE_SCHEMA,
                    "run_id": f"{result.run_id}:narrate",
                    "budget": {"tokens": tier.budget["tokens"]},
                }
            ),
            timeout=max(per_agent, 30.0),
        )
        narrative = dict(answer.get("json") or {})
        narrative.setdefault("status", "OK")
    except (TimeoutError, UpstreamError):
        narrative = degraded_narrative(record)
    record["narrative"] = narrative

    result.state = "DONE"
    result.seconds = time.perf_counter() - started
    return result
