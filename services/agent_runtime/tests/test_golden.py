"""T-043 — the Council against the golden cases (docs/12 §6).

Every Council agent, on every golden case, with a fake gateway. What is being
checked is not the model's judgement but the runtime's contract: a valid
opinion comes back, its factor score equals the tool that produced it, and
nothing it cites was invented.

The same run against the live model is the other half of the acceptance and
lives in the harness, because it takes minutes rather than seconds.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from httpx import AsyncClient

from ai.agents.bundle import ROOT as AGENT_ROOT
from ai.agents.bundle import load_bundle
from synthetic.golden import GOLDEN

COUNCIL = (
    "document_evidence",
    "policy_affordability",
    "credit_risk",
    "fraud_integrity",
    "member_relationship",
)

#: Which tool each agent's factor score must equal (docs/06 §5).
FACTOR_SOURCE = {
    "policy_affordability": ("CAPACITY", "affordability.compute", "capacity_score"),
    "credit_risk": ("CONDUCT", "risk.score", "conduct_score"),
    "fraud_integrity": ("INTEGRITY", "fraud.assess", "integrity_score"),
    "member_relationship": ("COMMITMENT", "member.commitment_score", "score"),
}


def grants_for(agent_id: str) -> list[str]:
    path = AGENT_ROOT / agent_id / "tools.yaml"
    return [g["name"] for g in (yaml.safe_load(path.read_text()) or {})["tools"]]


def opinion_for(case: Any, agent_id: str) -> dict[str, Any]:
    """An answer that satisfies the runtime, built from this agent's own tools.

    The evidence has to come from the results this agent was given, not from
    the case as a whole: the runtime rejects a claim citing an id the agent
    never received, which is the behaviour being relied on elsewhere.
    """
    evidence = sorted(
        {
            str(ref["evidence_id"])
            for result in case.results_for(grants_for(agent_id))
            for ref in result.get("evidence_refs") or []
        }
    )
    body: dict[str, Any] = {
        "stance": "REVIEW",
        "confidence": 0.7,
        "reason_codes": [],
        "claims": [{"text": "The case file was read.", "evidence_refs": evidence[:1]}] if evidence else [],
        "contradictions": [],
        "unresolved": [],
        "proposed_actions": [],
    }
    if agent_id in FACTOR_SOURCE:
        family, tool, field = FACTOR_SOURCE[agent_id]
        result = case.tool_results.get(tool)
        if result:
            payload = result["result"]
            body["factor_scores"] = {
                family: {
                    "score": payload[field],
                    "weight": 0.3,
                    "weighted": round(payload[field] * 0.3, 2),
                    "decisive": False,
                    "calc_id": payload.get("calc_id") or payload.get("conduct_calc_id") or "calc_unknown",
                }
            }
    return body


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c.scenario)
@pytest.mark.parametrize("agent_id", COUNCIL)
async def test_every_council_agent_produces_a_valid_opinion(
    client: AsyncClient, fake: Any, case: Any, agent_id: str
) -> None:
    """T-043 acceptance: a valid AgentOpinion on five golden snapshots."""
    fake.default = opinion_for(case, agent_id)
    body = (
        await client.post(
            "/agents/invoke",
            json={
                "agent_id": agent_id,
                "committee_run_id": f"run_{case.scenario}",
                "snapshot": case.snapshot,
                "tool_results": case.results_for(grants_for(agent_id)),
            },
        )
    ).json()

    assert body["degraded"] is False, body.get("detail")
    opinion = body["opinion"]
    assert opinion["agent_id"] == agent_id
    assert opinion["snapshot_id"] == case.snapshot["snapshot_id"]
    assert opinion["signature"].startswith("sha256:")


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c.scenario)
@pytest.mark.parametrize("agent_id", sorted(FACTOR_SOURCE))
async def test_a_factor_score_equals_the_tool_that_produced_it(
    client: AsyncClient, fake: Any, case: Any, agent_id: str
) -> None:
    """T-043 acceptance. An agent reports a tool's number; it never restates it."""
    family, tool, field = FACTOR_SOURCE[agent_id]
    result = case.tool_results.get(tool)
    if result is None:
        pytest.skip(f"{case.scenario} has no {tool}")

    fake.default = opinion_for(case, agent_id)
    body = (
        await client.post(
            "/agents/invoke",
            json={
                "agent_id": agent_id,
                "committee_run_id": f"run_{case.scenario}",
                "snapshot": case.snapshot,
                "tool_results": case.results_for(grants_for(agent_id)),
            },
        )
    ).json()

    scored = body["opinion"]["factor_scores"][family]
    assert scored["score"] == result["result"][field]
    assert scored["calc_id"]


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c.scenario)
async def test_a_golden_case_carries_evidence_for_what_it_shows(case: Any) -> None:
    """A case with tool results and no evidence ids could not be cited from."""
    assert case.tool_results
    assert case.evidence_ids()


@pytest.mark.parametrize("agent_id", COUNCIL)
def test_every_council_agent_can_reach_the_tools_it_is_granted(agent_id: str) -> None:
    """docs/06 §4 — a grant for a tool nobody implemented is a dead grant."""
    from ai.tools import tool_names

    unimplemented = set(grants_for(agent_id)) - set(tool_names())
    assert unimplemented == set(), unimplemented


@pytest.mark.parametrize("agent_id", COUNCIL)
def test_every_council_agent_declares_a_call_budget(agent_id: str) -> None:
    bundle = load_bundle(agent_id)
    assert bundle.call_budget >= len(bundle.tools)
