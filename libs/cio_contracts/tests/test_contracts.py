"""T-004 — contract schemas, generated models and the vocabularies agree.

The JSON Schemas are normative. These tests prove the generated Pydantic models
round-trip payloads the schemas accept, and that reason codes and events stay
inside the approved vocabulary (CLAUDE.md §2.2).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis_jsonschema import from_schema

import cio_contracts
from cio_contracts import models

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = ROOT / "contracts" / "schemas"

# contract -> generated model class
CONTRACT_MODELS: dict[str, Any] = {
    "ActionProposal": models.ActionProposal,
    "AgentOpinion": models.AgentOpinion,
    "ApprovalToken": models.ApprovalToken,
    "CaseSnapshot": models.CaseSnapshot,
    "CommitteeRun": models.CommitteeRun,
    "DecisionRecord": models.DecisionRecord,
    "EvidenceRef": models.EvidenceRef,
    "FactorScore": models.FactorScore,
    "HumanDecision": models.HumanDecision,
    "MemberEvent": models.MemberEvent,
    "PolicyResult": models.PolicyResult,
}

FILENAME = re.compile(r"^(?P<name>[a-z_]+)\.(?P<major>\d+)\.(?P<minor>\d+)\.json$")


def ulid(prefix: str) -> str:
    """A syntactically valid ULID for fixtures."""
    return f"{prefix}_01JQZK7M8N9P0Q1R2S3T4V5W6X"


SLOW = settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much],
)


def schema_files() -> list[Path]:
    return sorted(p for p in SCHEMA_DIR.glob("*.json") if FILENAME.match(p.name))


def bundled(contract: str) -> dict[str, Any]:
    doc = cio_contracts.bundle()
    return {"$ref": f"#/$defs/{contract}", "$defs": doc["$defs"]}


# ---------------------------------------------------------------------------
# schema hygiene
# ---------------------------------------------------------------------------
def test_every_documented_contract_exists() -> None:
    expected = {
        "case_snapshot.1.0.json",
        "evidence_ref.1.0.json",
        "agent_opinion.1.3.json",
        "decision_record.1.0.json",
        "human_decision.1.0.json",
        "action_proposal.1.0.json",
        "approval_token.1.0.json",
        "member_event.1.0.json",
        "policy_result.1.0.json",
        "factor_score.1.0.json",
        "committee_run.1.0.json",
    }
    present = {p.name for p in schema_files()}
    assert expected <= present, f"missing contracts: {sorted(expected - present)}"


@pytest.mark.parametrize("path", schema_files(), ids=lambda p: p.name)
def test_schema_id_matches_its_filename(path: Path) -> None:
    """docs/03: $id = https://cio.local/schemas/<name>/<major.minor>."""
    m = FILENAME.match(path.name)
    assert m
    raw = json.loads(path.read_text())
    expected = f"https://cio.local/schemas/{m['name']}/{m['major']}.{m['minor']}"
    assert raw["$id"] == expected, f"{path.name} declares $id {raw['$id']}"
    assert raw["$schema"].endswith("2020-12/schema")
    assert raw.get("title"), f"{path.name} has no title"


@pytest.mark.parametrize("contract", sorted(CONTRACT_MODELS), ids=str)
def test_contract_is_a_valid_json_schema(contract: str) -> None:
    cio_contracts.validator_for(contract)  # runs check_schema


@pytest.mark.parametrize("path", schema_files(), ids=lambda p: p.name)
def test_objects_forbid_unknown_fields(path: Path) -> None:
    """CLAUDE.md §2.2 — models are extra='forbid'; the schemas must agree."""
    raw = json.loads(path.read_text())

    def walk(node: Any, where: str) -> list[str]:
        bad: list[str] = []
        if isinstance(node, dict):
            if (
                node.get("type") == "object"
                and "properties" in node
                and node.get("additionalProperties", True) is not False
            ):
                bad.append(where)
            for key, value in node.items():
                bad += walk(value, f"{where}.{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                bad += walk(item, f"{where}[{i}]")
        return bad

    # `payload` and `parameters` are deliberately open (typed per event/action type)
    offenders = [
        w
        for w in walk(raw, path.stem)
        if not w.endswith((".payload", ".parameters", ".budgets", ".model_versions"))
    ]
    assert not offenders, f"objects allowing extra fields: {offenders}"


# ---------------------------------------------------------------------------
# round trips: schema -> instance -> pydantic -> json -> schema
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("contract", sorted(CONTRACT_MODELS), ids=str)
def test_generated_instances_round_trip(contract: str) -> None:
    model = CONTRACT_MODELS[contract]

    @SLOW
    @given(from_schema(bundled(contract)))
    def check(instance: Any) -> None:
        cio_contracts.validate(instance, contract)
        parsed = model.model_validate(instance)
        dumped = json.loads(parsed.model_dump_json(by_alias=True, exclude_unset=True))
        cio_contracts.validate(dumped, contract)

    check()


def test_validation_rejects_an_unknown_field() -> None:
    good = {
        "schema": "factor_score/1.0",
        "family": "CAPACITY",
        "score": 72,
        "calc_id": ulid("calc"),
        "tool": "affordability.compute",
        "inputs_digest": "0" * 64,
        "evidence_refs": [],
    }
    good.pop("schema")
    cio_contracts.validate(good, "FactorScore")
    with pytest.raises(cio_contracts.ContractViolation):
        cio_contracts.validate({**good, "surprise": 1}, "FactorScore")


def test_money_is_a_two_decimal_string_never_a_float() -> None:
    """CLAUDE.md §7 — money is a decimal string in JSON, never a float."""
    from jsonschema import Draft202012Validator

    defs = cio_contracts.bundle()["$defs"]
    money = Draft202012Validator({"$ref": "#/$defs/Common/$defs/Money", "$defs": defs})

    assert money.is_valid("12500.00")
    assert money.is_valid("-40.50")
    assert not money.is_valid(12500.00), "a float must not satisfy Money"
    assert not money.is_valid("12500"), "Money needs two decimal places"
    assert not money.is_valid("12,500.00"), "Money must not carry separators"


def _inlined_money_patterns(node: Any, where: str) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        pattern = node.get("pattern")
        if pattern and "[0-9]{2}" in pattern and node.get("type") == "string":
            found.append(where)
        for key, value in node.items():
            found += _inlined_money_patterns(value, f"{where}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += _inlined_money_patterns(value, f"{where}[{i}]")
    return found


@pytest.mark.parametrize(
    "path", [p for p in schema_files() if p.name != "common.1.0.json"], ids=lambda p: p.name
)
def test_every_money_field_uses_the_shared_definition(path: Path) -> None:
    """No contract may re-invent a money type with looser rules."""
    found = _inlined_money_patterns(json.loads(path.read_text()), path.stem)
    assert not found, f"{path.name} inlines a money pattern instead of $ref Money: {found}"


def test_override_reason_is_required_exactly_when_overriding() -> None:
    """docs/03 §7 — the rule the ledger depends on."""
    base = {
        "human_decision_id": ulid("hd"),
        "decision_record_id": ulid("dr"),
        "case_id": ulid("case"),
        "actor_id": "u-1",
        "authority_role": "SENIOR_OFFICER",
        "final_action": "APPROVE",
        "conditions": [],
        "evidence_acknowledged": [],
        "decided_at": "2026-09-05T10:00:00Z",
        "hash": "0" * 64,
        "prev_hash": "1" * 64,
        "schema": "human_decision/1.0",
    }
    cio_contracts.validate({**base, "override": False}, "HumanDecision")

    with pytest.raises(cio_contracts.ContractViolation):
        cio_contracts.validate({**base, "override": True}, "HumanDecision")

    reason = {"code": "OVR-01", "text": "Additional payslip obtained from the employer."}
    cio_contracts.validate({**base, "override": True, "override_reason": reason}, "HumanDecision")

    with pytest.raises(cio_contracts.ContractViolation):
        cio_contracts.validate({**base, "override": False, "override_reason": reason}, "HumanDecision")


def test_ovr_12_demands_a_longer_explanation() -> None:
    base = {
        "human_decision_id": ulid("hd"),
        "decision_record_id": ulid("dr"),
        "case_id": ulid("case"),
        "actor_id": "u-1",
        "authority_role": "SENIOR_OFFICER",
        "final_action": "DECLINE",
        "conditions": [],
        "evidence_acknowledged": [],
        "override": True,
        "decided_at": "2026-09-05T10:00:00Z",
        "hash": "0" * 64,
        "prev_hash": "1" * 64,
        "schema": "human_decision/1.0",
    }
    short = {"code": "OVR-12", "text": "x" * 30}
    long = {"code": "OVR-12", "text": "x" * 60}
    with pytest.raises(cio_contracts.ContractViolation):
        cio_contracts.validate({**base, "override_reason": short}, "HumanDecision")
    cio_contracts.validate({**base, "override_reason": long}, "HumanDecision")


def test_claims_must_cite_at_least_one_evidence_id() -> None:
    """CLAUDE.md §2.4 — evidence on every claim, enforced by the schema."""
    v = cio_contracts.validator_for("AgentOpinion")
    schema: dict[str, Any] = v.schema  # type: ignore[assignment]
    claims = schema["$defs"]["AgentOpinion"]["properties"]["claims"]["items"]
    assert claims["properties"]["evidence_refs"]["minItems"] == 1
    assert "evidence_refs" in claims["required"]


# ---------------------------------------------------------------------------
# vocabularies
# ---------------------------------------------------------------------------
def test_reason_codes_match_the_contract_pattern() -> None:
    doc = yaml.safe_load((ROOT / "contracts" / "reason_codes.yaml").read_text())
    pattern = re.compile(cio_contracts.bundle()["$defs"]["Common"]["$defs"]["ReasonCode"]["pattern"])
    codes = [c for group in doc["groups"].values() for c in group["codes"]]
    bad = [c for c in codes if not pattern.match(c)]
    assert not bad, f"reason codes outside the contract pattern: {bad}"
    assert len(codes) == len(set(codes)), "duplicate reason codes"


def test_every_reason_code_group_in_the_pattern_is_defined() -> None:
    doc = yaml.safe_load((ROOT / "contracts" / "reason_codes.yaml").read_text())
    pattern = cio_contracts.bundle()["$defs"]["Common"]["$defs"]["ReasonCode"]["pattern"]
    match = re.search(r"\^\(([^)]+)\)", pattern)
    assert match, "ReasonCode pattern no longer lists its groups"
    groups_in_pattern = set(match.group(1).split("|"))
    assert groups_in_pattern == set(doc["groups"]), "reason-code groups drifted from the pattern"


def test_override_codes_cover_ovr_01_to_12() -> None:
    doc = yaml.safe_load((ROOT / "contracts" / "reason_codes.yaml").read_text())
    assert set(doc["groups"]["OVR"]["codes"]) == {f"OVR-{i:02d}" for i in range(1, 13)}


def test_event_catalogue_is_well_formed() -> None:
    doc = yaml.safe_load((ROOT / "contracts" / "events.yaml").read_text())
    assert len(doc["events"]) >= 45, "event catalogue is shorter than docs/03 §12"
    for name, spec in doc["events"].items():
        assert name.endswith(".v1"), f"{name} is not versioned"
        for field in ("key", "producer", "consumers", "payload"):
            assert field in spec, f"{name} is missing {field}"
        assert isinstance(spec["consumers"], list) and spec["consumers"], f"{name} has no consumers"


def test_event_envelope_carries_correlation_ids() -> None:
    doc = yaml.safe_load((ROOT / "contracts" / "events.yaml").read_text())
    required = set(doc["envelope"]["required"])
    assert {
        "event_id",
        "name",
        "version",
        "key",
        "trace_id",
        "occurred_at",
        "producer",
        "payload",
    } <= required
