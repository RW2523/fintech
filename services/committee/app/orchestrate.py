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
from cio_common.ids import new_id

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


def _repairable(opinions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gaps the Challenger named that name a tool the orchestrator can call."""
    gaps: list[dict[str, Any]] = []
    for entry in opinions:
        if entry["opinion"].get("agent_id") != CHALLENGER:
            continue
        for item in entry["opinion"].get("unresolved") or []:
            if item.get("requested_tool"):
                gaps.append(item)
    return gaps


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
        while (
            tier.tier == "EXTENDED"
            and remaining() > 0
            and result.repair_loops < MAX_REPAIR_LOOPS
            and _repairable(result.opinions)
        ):
            result.state = "REPAIR"
            result.rounds.append("REPAIR")
            result.repair_loops += 1
            # The orchestrator calls the tool itself, with the workflow as
            # principal: an agent that could fill its own gap could decide
            # what counts as evidence.
            gaps = _repairable(result.opinions)
            affected = sorted(
                {
                    OWNS.get(agent, "") and agent
                    for agent in COUNCIL
                    if OWNS.get(agent) in {str(g.get("family")) for g in gaps}
                }
                - {""}
            )
            if not affected:
                affected = list(COUNCIL)

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
                        tool_results=tool_results.get(agent, []),
                        prior=[a["opinion"] for a in result.opinions],
                        budget_tokens=tokens_each,
                        timeout=min(per_agent, remaining()),
                    )
                    for agent in affected
                ]
            )
            result.opinions.extend(revised)
            break

    if remaining() <= 0:
        result.timed_out = True

    # --- SYNTHESIZE --------------------------------------------------------
    # Always. A deliberation that failed still produces a decision, and the
    # record says the deliberation failed.
    result.state = "SYNTHESIZE"
    result.rounds.append("SYNTHESIZE")
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
                "factor_scores": _factor_scores(result.opinions),
                "opinions": [entry["opinion"] for entry in result.opinions],
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
