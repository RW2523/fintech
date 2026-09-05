"""T-006 — purpose masking and automatic EvidenceRef attachment.

Two of the three acceptance criteria: outputs carry evidence refs, and fields
outside the caller's purpose come back masked rather than absent.
"""

from __future__ import annotations

import dataclasses

from cio_tools import (
    EVIDENCE_KEY,
    MASK,
    EvidenceSpec,
    Grant,
    GrantRegistry,
    PermittedUse,
    SideEffect,
    ToolContext,
    ToolRegistry,
    ToolSpec,
    build_evidence,
    extract_evidence_ids,
    mask_paths,
    masked_field_count,
)

UNDERWRITING = PermittedUse.UNDERWRITING
COLLECTIONS = PermittedUse.COLLECTIONS


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------
async def test_tool_output_carries_evidence_refs(reg: ToolRegistry, ctx: ToolContext) -> None:
    result = await reg.call("risk.score", {"snapshot_id": "snap_1"}, ctx)

    refs = result[EVIDENCE_KEY]
    assert len(refs) == 1
    ref = refs[0]
    assert ref["type"] == "MODEL_OUTPUT"
    assert ref["source_system"] == "risk-service"
    assert ref["evidence_id"].startswith("ev_")
    assert ref["locator"]["model_run_id"] == "mr_01JQZK7M8N9P0Q1R2S3T4V5W6X"
    assert ref["value"] == 0.021


async def test_evidence_refs_validate_against_the_contract(reg: ToolRegistry, ctx: ToolContext) -> None:
    """A minted ref must satisfy EvidenceRef 1.0, or agents cannot cite it."""
    import cio_contracts

    result = await reg.call("risk.score", {"snapshot_id": "snap_1"}, ctx)
    for ref in result[EVIDENCE_KEY]:
        cio_contracts.validate(ref, "EvidenceRef")


async def test_evidence_ids_are_unique_per_call(reg: ToolRegistry, ctx: ToolContext) -> None:
    first = await reg.call("history.get", {"member_id": ctx.member_id}, ctx)
    second = await reg.call("history.get", {"member_id": ctx.member_id}, ctx)
    assert extract_evidence_ids(first).isdisjoint(extract_evidence_ids(second))


async def test_the_invocation_record_lists_the_evidence_it_produced(
    reg: ToolRegistry, ctx: ToolContext
) -> None:
    """The runtime checks agent citations against exactly this set."""
    result = await reg.call("risk.score", {"snapshot_id": "snap_1"}, ctx)
    record = reg.invocations[-1]
    assert record.ok is True
    assert set(record.evidence_ids) == extract_evidence_ids(result)


async def test_a_tool_without_an_evidence_spec_attaches_none(reg: ToolRegistry) -> None:
    planner = ToolContext(agent_id="intervention_planner", run_id="run_1", purpose=COLLECTIONS)
    result = await reg.call("evidence.request", {"question": "confirm salary"}, planner)
    assert EVIDENCE_KEY not in result


def test_evidence_needs_a_locator_to_be_citable() -> None:
    """docs/03 §2 — locator must carry at least one key."""
    spec = EvidenceSpec(type="CORE_FIELD", source_system="core", locator_from={"field_path": "missing.path"})
    assert build_evidence({"present": 1}, spec, permitted_uses=frozenset({UNDERWRITING}), version="1.0") == []


def test_evidence_is_built_per_item_for_list_output() -> None:
    spec = EvidenceSpec(
        type="TIMELINE_EVENT",
        source_system="member_intelligence",
        items_path="events",
        source_record_path="event_id",
        locator_from={"event_id": "event_id"},
    )
    refs = build_evidence(
        {"events": [{"event_id": "e1"}, {"event_id": "e2"}, {"event_id": "e3"}]},
        spec,
        permitted_uses=frozenset({COLLECTIONS}),
        version="1.0",
    )
    assert [r["locator"]["event_id"] for r in refs] == ["e1", "e2", "e3"]
    assert all(r["permitted_uses"] == ["COLLECTIONS"] for r in refs)


def test_evidence_confidence_comes_from_the_record_when_present() -> None:
    spec = EvidenceSpec(
        type="DOCUMENT_FIELD",
        source_system="document-service",
        source_record_path="document_id",
        locator_from={"document_id": "document_id"},
        confidence_path="conf",
        default_confidence=0.5,
    )
    with_conf = build_evidence(
        {"document_id": "d1", "conf": 0.93}, spec, permitted_uses=frozenset({UNDERWRITING}), version="1.0"
    )
    without = build_evidence(
        {"document_id": "d2"}, spec, permitted_uses=frozenset({UNDERWRITING}), version="1.0"
    )
    assert with_conf[0]["confidence"] == 0.93
    assert without[0]["confidence"] == 0.5


# ---------------------------------------------------------------------------
# masking
# ---------------------------------------------------------------------------
async def test_fields_outside_the_purpose_are_masked_not_removed(reg: ToolRegistry, ctx: ToolContext) -> None:
    """A collections caller sees that a bureau grade exists but not its value."""
    collections = dataclasses.replace(ctx, agent_id="collections_copilot", purpose=COLLECTIONS)

    underwriting_view = await reg.call("history.get", {"member_id": ctx.member_id}, ctx)
    collections_view = await reg.call("history.get", {"member_id": ctx.member_id}, collections)

    assert underwriting_view["bureau_grade"] == "A"
    assert collections_view["bureau_grade"] == MASK
    assert collections_view["ontime_rate_24m"] == 0.96, "permitted fields stay intact"


async def test_masking_is_recorded_on_the_invocation(reg: ToolRegistry, ctx: ToolContext) -> None:
    collections = dataclasses.replace(ctx, agent_id="collections_copilot", purpose=COLLECTIONS)
    await reg.call("history.get", {"member_id": ctx.member_id}, collections)

    record = reg.invocations[-1]
    assert record.masked_paths == ("bureau_grade",)
    assert record.masked_values == 1


def test_mask_paths_reaches_into_nested_structures() -> None:
    payload = {"member": {"name": "A", "id": "M-1"}, "accounts": [{"iban": "X"}, {"iban": "Y"}]}
    masked = mask_paths(payload, ["member.name", "accounts.[].iban"])
    assert masked["member"]["name"] == MASK
    assert masked["member"]["id"] == "M-1"
    assert [a["iban"] for a in masked["accounts"]] == [MASK, MASK]


def test_mask_paths_leaves_the_original_untouched() -> None:
    payload = {"a": {"b": 1}}
    mask_paths(payload, ["a.b"])
    assert payload == {"a": {"b": 1}}, "masking must not mutate the caller's data"


def test_mask_paths_ignores_paths_that_are_not_present() -> None:
    assert mask_paths({"a": 1}, ["b.c"]) == {"a": 1}


def test_masked_field_count_walks_the_whole_payload() -> None:
    assert masked_field_count({"a": MASK, "b": [MASK, {"c": MASK}], "d": 1}) == 3


async def test_purposeless_fields_are_visible_to_every_purpose(reg: ToolRegistry) -> None:
    """Only fields with an explicit purpose list are ever masked."""
    registry = ToolRegistry(GrantRegistry([Grant("a", "open.get", 1)]))

    async def handler() -> dict:
        return {"anything": "visible"}

    registry.register(
        ToolSpec(
            name="open.get",
            version="1.0",
            handler=handler,
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {"anything": {"type": "string"}}},
            purpose_tags=frozenset({UNDERWRITING, COLLECTIONS}),
            side_effects=SideEffect.READ,
            backing_service="test",
        )
    )
    ctx = ToolContext(agent_id="a", run_id="run_1", purpose=COLLECTIONS)
    assert (await registry.call("open.get", {}, ctx))["anything"] == "visible"
