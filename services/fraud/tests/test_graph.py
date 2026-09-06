"""T-033 — the entity resolution graph (docs/07 §3)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.graph import (
    CONCENTRATION_HOLDERS,
    MAX_CYCLE,
    MIN_CYCLE,
    SERIAL_GUARANTOR_DEGREE,
    EntityGraph,
)

RING = tuple(f"M-{i:06d}" for i in range(1, 8))


def ring_graph(members: tuple[str, ...] = RING) -> EntityGraph:
    graph = EntityGraph()
    for index, member in enumerate(members):
        graph.add_guarantee(member, members[(index + 1) % len(members)])
    return graph


def applications(members: tuple[str, ...], *, days_apart: int = 15) -> dict[str, date]:
    start = date(2026, 6, 1)
    return {m: start + timedelta(days=i * days_apart) for i, m in enumerate(members)}


# ---------------------------------------------------------------------------
# cycles
# ---------------------------------------------------------------------------
def test_a_seven_member_ring_is_found_as_one_cycle() -> None:
    """T-033 acceptance: the planted ring is detected."""
    cycles = ring_graph().cycles()
    assert len(cycles) == 1
    assert set(cycles[0].members) == set(RING)
    assert cycles[0].length == 7


def test_a_ring_with_concurrent_borrowing_is_hot() -> None:
    """docs/07 §3 — three applications inside ninety days makes it HIGH."""
    cycle = ring_graph().cycles(applications=applications(RING[:4]))[0]
    assert cycle.is_hot
    assert len(cycle.applications) == 4
    assert cycle.window_days == 45


def test_a_quiet_ring_is_not_hot() -> None:
    """A cycle nobody is borrowing through is a shape, not an event."""
    assert not ring_graph().cycles()[0].is_hot


def test_borrowing_spread_beyond_the_window_is_not_hot() -> None:
    """Four applications a season apart are four decisions, not one scheme."""
    cycle = ring_graph().cycles(applications=applications(RING[:4], days_apart=120))[0]
    assert len(cycle.applications) == 4
    assert cycle.concurrent == 1
    assert not cycle.is_hot


def test_one_early_application_does_not_hide_a_cluster() -> None:
    """The window slides; it is not the spread of everything in the loop.

    Measured on the planted ring, taking the total spread meant a single
    member who had borrowed three months earlier stretched the measurement to
    107 days and the ring itself raised nothing.
    """
    from datetime import date as _date

    schedule = applications(RING[:4], days_apart=15)
    schedule[RING[4]] = _date(2026, 2, 1)
    cycle = ring_graph().cycles(applications=schedule)[0]
    assert len(cycle.applications) == 5
    assert cycle.concurrent == 4
    assert cycle.is_hot


def test_a_chain_that_does_not_close_is_not_a_cycle() -> None:
    graph = EntityGraph()
    for index in range(len(RING) - 1):
        graph.add_guarantee(RING[index], RING[index + 1])
    assert graph.cycles() == []


def test_direction_matters() -> None:
    """Two people guaranteeing each other is a two-cycle, not seven."""
    graph = EntityGraph()
    graph.add_guarantee("M-000001", "M-000002")
    graph.add_guarantee("M-000002", "M-000001")
    cycles = graph.cycles()
    assert len(cycles) == 1
    assert cycles[0].length == 2


def test_a_cycle_longer_than_the_bound_is_not_reported() -> None:
    """docs/07 §3 bounds cycles at eight members."""
    long_ring = tuple(f"M-{i:06d}" for i in range(1, MAX_CYCLE + 3))
    assert ring_graph(long_ring).cycles() == []


def test_the_shortest_reportable_cycle_is_two() -> None:
    graph = EntityGraph()
    graph.add_guarantee("M-000001", "M-000001")
    assert graph.cycles(minimum=MIN_CYCLE) == []


def test_a_member_never_guarantees_themselves() -> None:
    graph = EntityGraph()
    graph.add_guarantee("M-000001", "M-000001")
    assert graph.guarantees.number_of_edges() == 0


def test_each_cycle_is_reported_once_however_it_is_walked() -> None:
    cycles = ring_graph().cycles()
    assert len({frozenset(c.members) for c in cycles}) == len(cycles)


# ---------------------------------------------------------------------------
# serial guarantors and concentration
# ---------------------------------------------------------------------------
def test_a_serial_guarantor_is_found() -> None:
    graph = EntityGraph()
    for index in range(SERIAL_GUARANTOR_DEGREE):
        graph.add_guarantee("M-000100", f"M-0002{index:02d}")
    assert graph.serial_guarantors() == [("M-000100", SERIAL_GUARANTOR_DEGREE)]


def test_guaranteeing_a_few_people_is_not_serial() -> None:
    graph = EntityGraph()
    for index in range(SERIAL_GUARANTOR_DEGREE - 1):
        graph.add_guarantee("M-000100", f"M-0002{index:02d}")
    assert graph.serial_guarantors() == []


def test_a_branch_resting_on_three_people_is_a_concentration() -> None:
    graph = EntityGraph()
    guarantors = ["M-000001"] * 5 + ["M-000002"] * 4 + ["M-000003"] * 3 + [f"M-0001{i:02d}" for i in range(8)]
    notes = graph.concentration({"BR-01": guarantors})
    assert len(notes) == 1
    assert notes[0]["branch_id"] == "BR-01"
    assert notes[0]["share"] >= 0.40
    assert len(notes[0]["holders"]) == CONCENTRATION_HOLDERS


def test_a_spread_branch_is_not_a_concentration() -> None:
    graph = EntityGraph()
    assert graph.concentration({"BR-01": [f"M-0001{i:02d}" for i in range(40)]}) == []


def test_a_tiny_branch_is_not_reported_as_concentrated() -> None:
    """Three guarantees held by three people is not a finding, it is a small branch."""
    graph = EntityGraph()
    assert graph.concentration({"BR-01": ["M-000001", "M-000002", "M-000003"]}) == []


# ---------------------------------------------------------------------------
# shared attributes
# ---------------------------------------------------------------------------
def test_a_document_seen_under_two_names_is_reported() -> None:
    graph = EntityGraph()
    graph.add_document("M-000001", "phash-abc")
    graph.add_document("M-000002", "phash-abc")
    graph.add_document("M-000003", "phash-xyz")
    shared = graph.shared_documents()
    assert shared == [("phash-abc", ("M-000001", "M-000002"))]


def test_a_contact_shared_by_two_members_is_reported() -> None:
    graph = EntityGraph()
    graph.add_contact("M-000001", "+10000000")
    graph.add_contact("M-000002", "+10000000")
    assert graph.shared_contacts() == [("+10000000", ("M-000001", "M-000002"))]


def test_an_unshared_attribute_is_not_reported() -> None:
    graph = EntityGraph()
    graph.add_document("M-000001", "phash-abc")
    graph.add_contact("M-000001", "+10000000")
    assert graph.shared_documents() == []
    assert graph.shared_contacts() == []


# ---------------------------------------------------------------------------
# the subgraph a person reads
# ---------------------------------------------------------------------------
def test_the_subgraph_covers_the_whole_ring() -> None:
    """A cycle finding is unreadable without the rest of the cycle."""
    graph = ring_graph()
    view = graph.subgraph_around(RING, radius=1)
    assert len(view["nodes"]) == len(RING)
    assert len(view["edges"]) == len(RING)
    assert all(n["seed"] for n in view["nodes"])


def test_the_subgraph_marks_which_members_the_case_is_about() -> None:
    view = ring_graph().subgraph_around([RING[0]], radius=1)
    seeds = [n["ref"] for n in view["nodes"] if n["seed"]]
    assert seeds == [RING[0]]
    assert len(view["nodes"]) > 1


def test_an_unknown_member_yields_an_empty_subgraph() -> None:
    view = ring_graph().subgraph_around(["M-999999"])
    assert view["nodes"] == []
    assert view["edges"] == []


def test_employment_and_guarantees_share_one_graph() -> None:
    graph = EntityGraph()
    graph.add_employment("M-000001", "E-001")
    graph.add_guarantee("M-000001", "M-000002")
    view = graph.subgraph_around(["M-000001"], radius=1)
    kinds = {n["kind"] for n in view["nodes"]}
    assert kinds == {"member", "employer"}


@pytest.mark.parametrize("size", [2, 3, 5, 8])
def test_cycles_of_every_permitted_length_are_found(size: int) -> None:
    members = tuple(f"M-{i:06d}" for i in range(1, size + 1))
    cycles = ring_graph(members).cycles()
    assert len(cycles) == 1
    assert cycles[0].length == size
