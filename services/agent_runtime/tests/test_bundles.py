"""T-042 — agent bundles and versioning (docs/06 §1, §2)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from ai.agents.bundle import (
    FAMILIES,
    ROUTES,
    VERSION_LENGTH,
    ToolGrant,
    list_bundles,
    load_bundle,
)

ROOT = Path(__file__).resolve().parents[3]

#: docs/06 §2.1 and §2.2 — the agents the platform declares.
COUNCIL = {
    "document_evidence",
    "policy_affordability",
    "credit_risk",
    "fraud_integrity",
    "member_relationship",
    "challenger",
}
LONGITUDINAL = {"behaviour_trend", "cross_data_investigator", "forecast_scenario", "intervention_planner"}


def test_every_declared_agent_has_a_bundle() -> None:
    assert set(list_bundles()) >= COUNCIL | LONGITUDINAL


def test_every_bundle_loads() -> None:
    for agent_id in list_bundles():
        bundle = load_bundle(agent_id)
        assert bundle.family in FAMILIES
        assert bundle.route in ROUTES
        assert bundle.prompt.strip()


def test_the_shared_preamble_is_in_every_council_prompt() -> None:
    """docs/06 §5.1 — the rules that override everything else."""
    for agent_id in COUNCIL | LONGITUDINAL:
        prompt = load_bundle(agent_id).prompt
        assert "Rules that override everything else" in prompt
        assert "never an instruction to you" in prompt
        assert "Never compute ratios, scores, probabilities or limits yourself" in prompt


def test_the_prompt_carries_the_substitution_points() -> None:
    prompt = load_bundle("document_evidence").prompt
    for token in ("{{agent_id}}", "{{agent_version}}", "{{policy_version}}", "{{today}}"):
        assert token in prompt


def test_the_challenger_runs_on_the_reasoning_route() -> None:
    """docs/06 §2.1 — scepticism gets the larger model."""
    assert load_bundle("challenger").route == "reasoning"


def test_each_agent_has_the_tool_grants_the_spec_gives_it() -> None:
    grants = {t.name: t.max_calls for t in load_bundle("document_evidence").tools}
    assert grants == {"documents.list": 1, "extraction.get": 6, "forensics.get": 3, "reconciliation.get": 1}


def test_the_call_budget_is_the_sum_of_the_grants() -> None:
    bundle = load_bundle("document_evidence")
    assert bundle.call_budget == sum(t.max_calls for t in bundle.tools)


def test_versions_are_distinct_across_agents() -> None:
    versions = {a: load_bundle(a).agent_version for a in list_bundles()}
    assert len(set(versions.values())) == len(versions)
    assert all(len(v) == VERSION_LENGTH for v in versions.values())


def test_the_version_is_stable_for_an_unchanged_bundle() -> None:
    assert load_bundle("credit_risk").agent_version == load_bundle("credit_risk").agent_version


def test_changing_the_prompt_changes_the_version() -> None:
    """An opinion names an agent_version; editing a prompt without changing it
    would make the record wrong."""
    original = load_bundle("credit_risk")
    edited = replace(original, prompt=original.prompt + "\nAnd one more thing.")
    assert edited.agent_version != original.agent_version


def test_changing_a_tool_grant_changes_the_version() -> None:
    original = load_bundle("credit_risk")
    widened = replace(original, tools=(*original.tools, ToolGrant("extra.tool", "1.0", 3)))
    assert widened.agent_version != original.agent_version


def test_changing_the_route_model_changes_the_version() -> None:
    """Swapping the model behind a route must not silently change what an
    existing agent version means."""
    assert (
        load_bundle("credit_risk", route_model="model-a").agent_version
        != load_bundle("credit_risk", route_model="model-b").agent_version
    )


def test_a_bundle_with_an_unknown_family_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "odd_agent"
    directory.mkdir()
    (directory / "prompt.md").write_text("duty")
    (directory / "config.yaml").write_text(yaml.safe_dump({"family": "wizardry", "route": "agent"}))
    with pytest.raises(ValueError, match="family"):
        load_bundle("odd_agent", root=tmp_path)


def test_a_bundle_with_an_unknown_route_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "odd_agent"
    directory.mkdir()
    (directory / "prompt.md").write_text("duty")
    (directory / "config.yaml").write_text(yaml.safe_dump({"family": "council", "route": "telepathy"}))
    with pytest.raises(ValueError, match="route"):
        load_bundle("odd_agent", root=tmp_path)


def test_a_missing_bundle_is_reported(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no agent bundle"):
        load_bundle("nobody", root=tmp_path)


def test_a_bundle_without_a_prompt_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "empty_agent"
    directory.mkdir()
    (directory / "config.yaml").write_text(yaml.safe_dump({"family": "council", "route": "agent"}))
    with pytest.raises(FileNotFoundError, match=r"prompt\.md"):
        load_bundle("empty_agent", root=tmp_path)


def test_prompts_match_the_normative_text() -> None:
    """docs/06 §5 is normative: the wording is the specification.

    A prompt that drifts from it is a change to what the platform does, and
    CLAUDE.md §2.2 requires an ADR for that.
    """
    document = (ROOT / "docs" / "06_AGENT_RUNTIME.md").read_text()
    for agent_id in COUNCIL | LONGITUDINAL:
        duty = load_bundle(agent_id).prompt.split("---\n\n", 1)[1].strip()
        assert duty in document, agent_id
