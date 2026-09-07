"""T-010 — policy packs load, validate and reject the mistakes that matter.

Schema validation catches shape errors. The semantic checks catch the ones that
would silently change a decision: weights that do not sum to 1, thresholds in
the wrong order, an authority ladder that does not ascend.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.packs import PackError, PolicyPack, available_packs, load_pack, semantic_errors

ROOT = Path(__file__).resolve().parents[3]
PACKS = available_packs()


@pytest.fixture(scope="module")
def std() -> PolicyPack:
    return load_pack("PF-STD", "2026.09.1")


@pytest.fixture(scope="module")
def shariah() -> PolicyPack:
    return load_pack("PF-SHARIAH", "2026.09.1")


def _bodies() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    base = ROOT / "policy_packs" / "PF-STD" / "2026.09.1"
    return tuple(  # type: ignore[return-value]
        yaml.safe_load((base / f"{kind}.yaml").read_text()) for kind in ("policy", "dff", "autonomy")
    )


# ---------------------------------------------------------------------------
# both shipped packs are valid
# ---------------------------------------------------------------------------
def test_both_products_ship_a_pack() -> None:
    assert {p for p, _ in PACKS} == {"PF-STD", "PF-SHARIAH"}


@pytest.mark.parametrize("product,version", PACKS, ids=lambda v: str(v))
def test_every_pack_on_disk_loads(product: str, version: str) -> None:
    pack = load_pack(product, version)
    assert pack.policy_version == f"policy/{product}/{version}"
    assert pack.dff_version == f"dff/{product}/{version}"
    assert pack.autonomy_version == f"autonomy/{product}/{version}"


def test_gate_rules_are_returned_in_evaluation_order(std: PolicyPack) -> None:
    """docs/05 §3.2 — eligibility, documents, shariah, affordability, exposure."""
    categories = [r["category"] for r in std.rules()]
    first_seen = list(dict.fromkeys(categories))
    assert first_seen == ["ELIGIBILITY", "DOCUMENTS", "AFFORDABILITY", "EXPOSURE"]


def test_shariah_gates_run_before_affordability(shariah: PolicyPack) -> None:
    categories = [r["category"] for r in shariah.rules()]
    assert categories.index("SHARIAH") < categories.index("AFFORDABILITY")


def test_authority_bands_pick_the_smallest_sufficient_role(std: PolicyPack) -> None:
    assert std.authority_for(1) == "CREDIT_OFFICER"
    assert std.authority_for(20000) == "CREDIT_OFFICER"
    assert std.authority_for(20001) == "SENIOR_OFFICER"
    assert std.authority_for(75000) == "SENIOR_OFFICER"
    assert std.authority_for(75001) == "CREDIT_COMMITTEE"
    assert std.authority_for(10_000_000) == "CREDIT_COMMITTEE"


def test_exposure_limits_cover_every_grade(std: PolicyPack) -> None:
    assert [std.exposure_limit(g) for g in "ABCDE"] == [200000, 150000, 100000, 60000, 30000]
    assert std.exposure_limit("Z") is None


# ---------------------------------------------------------------------------
# the packs match the documented product rules
# ---------------------------------------------------------------------------
def test_pf_std_carries_the_documented_gates(std: PolicyPack) -> None:
    ids = {r["id"] for r in std.rules()}
    assert {
        "ELG-01",
        "ELG-02",
        "ELG-03",
        "ELG-04",
        "ELG-05",
        "DOC-01",
        "DOC-04",
        "AFF-01",
        "AFF-02",
        "AFF-03",
        "AFF-04",
        "EXP-01",
    } == ids


def test_pf_std_thresholds_match_the_spec(std: PolicyPack) -> None:
    assert std.policy["affordability"]["dsr_limit"] == 0.60
    assert std.policy["affordability"]["residual_income_min"] == 800
    assert std.dff["thresholds"] == {"approve": 70, "decline": 45}
    assert std.dff["min_evidence_coverage"] == 0.90


def test_shariah_excludes_debt_consolidation_and_rate_increases(shariah: PolicyPack) -> None:
    """docs/05 §2.1 — the two substantive product differences."""
    assert "DEBT_CONSOLIDATION" not in shariah.policy["product_terms"]["purposes_allowed"]
    assert shariah.policy["product_terms"]["structure"] == "MURABAHAH"
    options = shariah.policy["collections"]["restructure_options"]
    assert "RESCHEDULE_EXTEND_24M" not in options
    assert {"SHR-01", "SHR-03", "SHR-04"} <= {r["id"] for r in shariah.rules()}


def test_autonomy_starts_at_assist(std: PolicyPack) -> None:
    """docs/10 §"Assumptions" — the demo starts at ASSIST, not autonomous."""
    assert std.autonomy["setting"] == "ASSIST"


def test_early_warning_cases_may_never_execute_an_l3_action(std: PolicyPack) -> None:
    """docs/05 §6 — the hard limit on the longitudinal path."""
    assert std.autonomy["prohibited_for_case_type"]["EARLY_WARNING"] == ["L3"]


def test_the_kill_switch_needs_two_named_owners(std: PolicyPack) -> None:
    owners = std.autonomy["kill_switch"]["owners"]
    assert set(owners) == {"HEAD_OF_CREDIT", "HEAD_OF_RISK"}
    assert std.autonomy["kill_switch"]["effect"]["revert_to"] == "ADVISE"


def test_every_reason_code_a_rule_cites_exists_in_the_vocabulary() -> None:
    """A rule may only cite an approved reason code (docs/03 §11)."""
    vocabulary = {
        code
        for group in yaml.safe_load((ROOT / "contracts" / "reason_codes.yaml").read_text())["groups"].values()
        for code in group["codes"]
    }
    for product, version in PACKS:
        pack = load_pack(product, version)
        cited = {r["reason_code"] for r in pack.rules()}
        assert cited <= vocabulary, f"{product} cites unknown codes: {sorted(cited - vocabulary)}"


def test_every_rule_declares_the_context_keys_it_reads() -> None:
    """`reads` is what the engine turns into EvidenceRefs (docs/05 §3.1)."""
    for product, version in PACKS:
        for rule in load_pack(product, version).rules():
            assert rule["reads"], f"{product} {rule['id']} declares no reads"
            for key in rule["reads"]:
                assert re.match(r"^[a-z_]+(\.[a-z_0-9]+)+$", key), (
                    f"{product} {rule['id']} reads a malformed key: {key}"
                )


# ---------------------------------------------------------------------------
# broken packs are rejected
# ---------------------------------------------------------------------------
def test_a_missing_file_is_reported() -> None:
    with pytest.raises(PackError, match="missing"):
        load_pack("PF-STD", "1999.01.1")


@pytest.mark.parametrize(
    "mutate,expected",
    [
        pytest.param(
            lambda p, d, a: d["weights"].__setitem__("CAPACITY", 0.50),
            "weights sum to",
            id="weights-do-not-sum-to-one",
        ),
        pytest.param(
            lambda p, d, a: d["thresholds"].__setitem__("decline", 80),
            "thresholds are not ordered",
            id="decline-above-approve",
        ),
        pytest.param(
            lambda p, d, a: p["authority"]["bands"].__setitem__(
                0, {"max_amount": 90000, "role": "CREDIT_OFFICER"}
            ),
            "do not ascend",
            id="authority-bands-descend",
        ),
        pytest.param(
            lambda p, d, a: p["authority"]["bands"].__setitem__(
                2, {"max_amount": 500000, "role": "CREDIT_COMMITTEE"}
            ),
            "open-ended",
            id="no-open-ended-band",
        ),
        pytest.param(
            lambda p, d, a: d["weights"].pop("CONDITIONS"), "CONDITIONS", id="scored-family-without-a-weight"
        ),
        pytest.param(
            lambda p, d, a: p["product_terms"].__setitem__("min_amount", 999999),
            "min_amount is not below max_amount",
            id="inverted-product-amounts",
        ),
        pytest.param(
            lambda p, d, a: p["eligibility"]["rules"][1].__setitem__("id", "ELG-01"),
            "duplicate rule ids",
            id="duplicate-rule-id",
        ),
        pytest.param(
            lambda p, d, a: d.__setitem__("product", "PF-OTHER"),
            "disagree on product",
            id="files-describe-different-products",
        ),
        pytest.param(
            lambda p, d, a: a.__setitem__("version", "2027.01.1"),
            "disagree on version",
            id="files-describe-different-versions",
        ),
        pytest.param(
            lambda p, d, a: p["eligibility"]["rules"][0].pop("reason_code"),
            "reason",
            id="blocking-gate-without-a-reason-code",
        ),
        pytest.param(
            lambda p, d, a: p["documents"]["critical_fields"].__setitem__("PAYSLIP_OTHER", ["x"]),
            "not required",
            id="critical-fields-for-an-optional-document",
        ),
        pytest.param(
            lambda p, d, a: a["autonomous_conditions"].__setitem__("min_confidence", 0.2),
            "bounded dial",
            id="autonomy-confidence-too-low",
        ),
    ],
)
def test_a_broken_pack_is_rejected(mutate: Any, expected: str) -> None:
    policy, dff, autonomy = _bodies()
    mutate(policy, dff, autonomy)
    problems = semantic_errors(policy, dff, autonomy)
    assert any(expected in p for p in problems), f"expected a problem mentioning {expected!r}, got {problems}"


def test_a_valid_pack_produces_no_semantic_problems() -> None:
    assert semantic_errors(*_bodies()) == []


def test_load_pack_reports_every_problem_at_once(tmp_path: Path) -> None:
    """An operator should see the whole list, not the first failure."""
    policy, dff, autonomy = _bodies()
    dff["weights"]["CAPACITY"] = 0.50
    dff["thresholds"]["decline"] = 80
    base = tmp_path / "PF-BROKEN" / "2026.09.1"
    base.mkdir(parents=True)
    (tmp_path / "schema").symlink_to(ROOT / "policy_packs" / "schema")
    for kind, body in (("policy", policy), ("dff", dff), ("autonomy", autonomy)):
        (base / f"{kind}.yaml").write_text(yaml.safe_dump(body))

    with pytest.raises(PackError) as exc:
        load_pack("PF-BROKEN", "2026.09.1", root=tmp_path)
    assert len(exc.value.problems) >= 2


def test_a_malformed_rule_fails_schema_validation(tmp_path: Path) -> None:
    policy, dff, autonomy = _bodies()
    policy["eligibility"]["rules"][0]["on_fail"] = "MAYBE"
    base = tmp_path / "PF-BROKEN" / "2026.09.1"
    base.mkdir(parents=True)
    (tmp_path / "schema").symlink_to(ROOT / "policy_packs" / "schema")
    for kind, body in (("policy", policy), ("dff", dff), ("autonomy", autonomy)):
        (base / f"{kind}.yaml").write_text(yaml.safe_dump(body))

    with pytest.raises(PackError) as exc:
        load_pack("PF-BROKEN", "2026.09.1", root=tmp_path)
    assert any("on_fail" in p for p in exc.value.problems)


def test_packs_are_not_mutated_by_loading(std: PolicyPack) -> None:
    before = copy.deepcopy(std.policy)
    std.rules()
    std.authority_for(50000)
    assert std.policy == before


def test_the_newest_version_is_last_past_the_ninth(tmp_path: Path) -> None:
    """Sorted as text, 2026.09.10 comes before 2026.09.9.

    Which meant that from a product's tenth version onward, `latest` returned
    an older pack: adoption reported the new version, the sandbox drill showed
    the old one still in force, and every case decided in between was decided
    under weights nobody had adopted. Found by the drill, not by a test —
    nothing here had ever created a tenth version.
    """
    product = tmp_path / "PF-STD"
    for n in list(range(1, 13)):
        version = product / f"2026.09.{n}"
        version.mkdir(parents=True)
        (version / "policy.yaml").write_text("{}")

    versions = [v for p, v in available_packs(tmp_path) if p == "PF-STD"]
    assert versions[-1] == "2026.09.12"
    assert versions[:3] == ["2026.09.1", "2026.09.2", "2026.09.3"]


def test_a_version_that_is_not_a_number_still_sorts(tmp_path: Path) -> None:
    """An oddly named directory orders predictably rather than raising."""
    product = tmp_path / "PF-STD"
    for name in ("2026.09.2", "2026.09.10", "2026.09.draft"):
        version = product / name
        version.mkdir(parents=True)
        (version / "policy.yaml").write_text("{}")

    versions = [v for p, v in available_packs(tmp_path) if p == "PF-STD"]
    assert versions == ["2026.09.2", "2026.09.10", "2026.09.draft"]
