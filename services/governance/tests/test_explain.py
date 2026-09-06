"""T-034 — the structured explanation (docs/07 §6)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.explain import InventedIdentifierError, build_explanation, collect_identifiers
from tests.conftest import (
    CALC_ID,
    EVIDENCE_ID,
    FINDING_ID,
    HUMAN_ID,
    MODEL_RUN_ID,
    RECORD_ID,
)


# ---------------------------------------------------------------------------
# the five levels
# ---------------------------------------------------------------------------
async def test_the_explanation_has_the_five_levels(client: AsyncClient) -> None:
    body = (await client.post("/explain/factors", json={"decision_record_id": RECORD_ID})).json()
    assert set(body["levels"]) == {"policy", "factors", "drivers", "provenance", "human"}


async def test_policy_reasons_carry_their_clause(client: AsyncClient) -> None:
    """A reason code without the clause it came from cannot be checked."""
    policy = (await client.post("/explain/factors", json={"decision_record_id": RECORD_ID})).json()["levels"][
        "policy"
    ]
    assert policy
    for entry in policy:
        assert entry["rule_id"]
        assert entry["clause_id"]


async def test_a_failed_gate_is_marked_blocking(client: AsyncClient) -> None:
    policy = (await client.post("/explain/factors", json={"decision_record_id": RECORD_ID})).json()["levels"][
        "policy"
    ]
    blocking = [p for p in policy if p["blocking"]]
    assert [p["rule_id"] for p in blocking] == ["CAP-02"]


async def test_the_factor_table_marks_what_decided_it(client: AsyncClient) -> None:
    factors = (await client.post("/explain/factors", json={"decision_record_id": RECORD_ID})).json()[
        "levels"
    ]["factors"]
    decisive = [f["family"] for f in factors if f["decisive"]]
    assert decisive == ["CAPACITY"]
    assert all(f["calc_id"] for f in factors)


async def test_factors_are_listed_in_the_frameworks_order(client: AsyncClient) -> None:
    factors = (await client.post("/explain/factors", json={"decision_record_id": RECORD_ID})).json()[
        "levels"
    ]["factors"]
    assert [f["family"] for f in factors] == ["CAPACITY", "CONDUCT", "INTEGRITY"]


async def test_model_drivers_carry_approved_reason_codes(client: AsyncClient) -> None:
    drivers = (await client.post("/explain/factors", json={"decision_record_id": RECORD_ID})).json()[
        "levels"
    ]["drivers"]
    assert drivers
    assert all(d["reason_code"] for d in drivers)
    assert all(d["model_run_id"] == MODEL_RUN_ID for d in drivers)


async def test_provenance_covers_evidence_documents_and_findings(client: AsyncClient) -> None:
    provenance = (await client.post("/explain/factors", json={"decision_record_id": RECORD_ID})).json()[
        "levels"
    ]["provenance"]
    systems = {p["source_system"] for p in provenance}
    assert {"document", "fraud"} <= systems
    assert any(p.get("evidence_id") == EVIDENCE_ID for p in provenance)
    assert any(p.get("finding_id") == FINDING_ID for p in provenance)


async def test_the_human_decision_records_the_override(client: AsyncClient) -> None:
    human = (await client.post("/explain/factors", json={"decision_record_id": RECORD_ID})).json()["levels"][
        "human"
    ]
    assert human["human_decision_id"] == HUMAN_ID
    assert human["override"] is True
    assert human["override_reason_code"] == "OVR-03"


async def test_counterfactuals_come_from_the_record(client: AsyncClient) -> None:
    body = (await client.post("/explain/factors", json={"decision_record_id": RECORD_ID})).json()
    assert body["counterfactuals"] == [
        {"condition": "instalment reduced by 12%", "new_recommendation": "APPROVE"}
    ]


# ---------------------------------------------------------------------------
# the rule that matters: nothing invented
# ---------------------------------------------------------------------------
async def test_every_identifier_appears_in_the_run(client: AsyncClient) -> None:
    """T-034 acceptance: narrative inputs contain only ids present in the run."""
    body = (await client.post("/explain/factors", json={"decision_record_id": RECORD_ID})).json()
    cited = collect_identifiers(body)
    assert cited
    assert cited <= {
        RECORD_ID,
        MODEL_RUN_ID,
        EVIDENCE_ID,
        CALC_ID,
        HUMAN_ID,
        FINDING_ID,
        "snap_01ARZ3NDEKTSV4RRFFQ69G5FAW",
        "fs_01ARZ3NDEKTSV4RRFFQ69G5FB2",
        "doc_01ARZ3NDEKTSV4RRFFQ69G5FB3",
    }


def test_an_identifier_only_the_explanation_knows_is_refused(bundle: Any) -> None:
    """An explanation citing something the run never produced is a defect.

    It is refused rather than trimmed: dropping the id quietly would hide
    whatever assembled it, and a narrative built on it would read as proof.
    Here the model run is the only place the id could have come from, and it
    is removed after the drivers are read, so the citation has no source.
    """
    invented = "ev_01ARZ3NDEKTSV4RRFFQ69G5FZZ"

    class Leaky(dict):
        pass

    bundle.model_run["evidence_refs"] = [
        {
            "evidence_id": invented,
            "type": "ANALYTIC_RESULT",
            "source_system": "test",
            "source_record_id": "x",
            "locator": {},
            "captured_at": "2026-05-01T00:00:00+00:00",
        }
    ]
    # Traceable while the record is present.
    assert invented in collect_identifiers(build_explanation(bundle).as_dict())

    # The assembler keeps the reference but the record is gone.
    from app import explain as explain_module

    original = explain_module._provenance_level
    explain_module._provenance_level = lambda _b: [{"evidence_id": invented}]
    try:
        bundle.model_run = None
        with pytest.raises(InventedIdentifierError) as caught:
            build_explanation(bundle)
        assert invented in caught.value.unknown
    finally:
        explain_module._provenance_level = original


def test_a_driver_naming_an_unknown_run_is_refused(bundle: Any) -> None:
    bundle.model_run["model_run_id"] = "mr_01ARZ3NDEKTSV4RRFFQ69G5FZZ"
    bundle.model_run["drivers"] = [
        {"feature": "x", "direction": "ADVERSE", "contribution": 1.0, "share": 1.0, "reason_code": "CAP-02"}
    ]
    # The run id is now in the model_run record, so it is traceable.
    build_explanation(bundle)

    # Remove the record entirely: the id can no longer be traced anywhere.
    run = bundle.model_run
    bundle.model_run = None
    explanation = build_explanation(bundle)
    assert explanation.drivers == []
    bundle.model_run = run


def test_identifiers_are_found_wherever_they_hide() -> None:
    node = {
        "a": ["ev_01ARZ3NDEKTSV4RRFFQ69G5FAV"],
        "mr_01ARZ3NDEKTSV4RRFFQ69G5FAW": {"b": "text"},
        "c": {"d": ("calc_01ARZ3NDEKTSV4RRFFQ69G5FAX",)},
    }
    assert collect_identifiers(node) == {
        "ev_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "mr_01ARZ3NDEKTSV4RRFFQ69G5FAW",
        "calc_01ARZ3NDEKTSV4RRFFQ69G5FAX",
    }


def test_ordinary_text_is_not_mistaken_for_an_identifier() -> None:
    assert collect_identifiers({"note": "approved after review, see case notes"}) == set()


def test_the_leak_error_names_what_was_invented() -> None:
    with pytest.raises(InventedIdentifierError, match="ev_"):
        raise InventedIdentifierError({"ev_01ARZ3NDEKTSV4RRFFQ69G5FZZ"})


# ---------------------------------------------------------------------------
# missing pieces
# ---------------------------------------------------------------------------
def test_an_absent_record_is_named_not_filled_in(bundle: Any) -> None:
    """Fail safe: what could not be read is listed, never invented."""
    bundle.model_run = None
    bundle.sources["model_run"] = "unavailable"
    explanation = build_explanation(bundle)
    assert explanation.drivers == []
    assert "model_run" in explanation.unavailable


def test_a_decision_without_a_human_step_says_so(bundle: Any) -> None:
    bundle.human_decision = None
    assert build_explanation(bundle).human is None


async def test_an_unknown_decision_record_is_not_found(client: AsyncClient) -> None:
    response = await client.post("/explain/factors", json={"decision_record_id": "dr_missing"})
    assert response.status_code == 404


async def test_a_malformed_request_is_refused(client: AsyncClient) -> None:
    assert (await client.post("/explain/factors", json={})).status_code == 422
    assert (
        await client.post("/explain/factors", json={"decision_record_id": "dr_1", "extra": 1})
    ).status_code == 422


# ---------------------------------------------------------------------------
# the model inventory
# ---------------------------------------------------------------------------
async def test_the_inventory_lists_every_family(client: AsyncClient) -> None:
    body = (await client.get("/governance/models")).json()
    families = {row["family"] for row in body["families"]}
    assert {"credit_risk", "fraud", "lmi"} <= families


async def test_an_untrained_family_is_listed_not_hidden(client: AsyncClient) -> None:
    """An operator needs to see what is missing, not just what is there."""
    body = (await client.get("/governance/models")).json()
    untrained = [row for row in body["families"] if not row["trained"]]
    for row in untrained:
        assert "train.py" in row["detail"]


async def test_a_trained_family_names_what_is_serving(client: AsyncClient) -> None:
    body = (await client.get("/governance/models")).json()
    for row in body["families"]:
        if not row["trained"]:
            continue
        assert row["serving"]
        serving = [v for v in row["versions"] if v["serving"]]
        assert len(serving) == 1
        assert serving[0]["version"] == row["serving"]


async def test_the_inventory_carries_the_headline_numbers(client: AsyncClient) -> None:
    body = (await client.get("/governance/models")).json()
    credit = next(r for r in body["families"] if r["family"] == "credit_risk")
    if credit["trained"]:
        assert "holdout_auc" in credit["versions"][0]["headline"]


async def test_a_card_is_served_as_written(client: AsyncClient) -> None:
    response = await client.get("/governance/models/credit_risk/card")
    if response.status_code == 404:
        pytest.skip("credit-risk model not trained")
    body = response.json()
    assert body["card"].startswith("# Model card")
    assert body["metrics"]["family"] == "credit_risk"


async def test_an_unknown_family_has_no_card(client: AsyncClient) -> None:
    assert (await client.get("/governance/models/wizardry/card")).status_code == 404


async def test_an_unknown_version_has_no_card(client: AsyncClient) -> None:
    assert (await client.get("/governance/models/credit_risk/1999.01.1/card")).status_code == 404
