"""The entity resolution graph (docs/07 §3).

Fraud that matters is rarely visible in one application. It shows up in the
shape of the relationships around it: the same document appearing under two
names, a handful of people guaranteeing each other in a closed loop, one
person standing behind a whole branch. So the service builds a graph and asks
questions of the shape, not of the applicant.

Nodes are members, employers, documents (by perceptual hash) and contacts.
Edges are the four relationships docs/07 §3 names. Nothing here is a
judgement: the graph reports what is connected, and the rules decide what that
means.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import networkx as nx

__all__ = [
    "CONCENTRATION_HOLDERS",
    "CONCENTRATION_SHARE",
    "MAX_CYCLE",
    "MIN_CYCLE",
    "SERIAL_GUARANTOR_DEGREE",
    "Cycle",
    "EntityGraph",
]

#: docs/07 §3 — cycles of length 2 to 8 among guarantee edges.
MIN_CYCLE = 2
MAX_CYCLE = 8

#: docs/07 §3 — a cycle is HIGH when the people in it are also borrowing at
#: the same time, which is what separates a ring from a village.
CYCLE_HIGH_APPLICATIONS = 3
CYCLE_WINDOW_DAYS = 90

#: docs/07 §3 — someone guaranteeing this many people is doing something other
#: than helping a friend.
SERIAL_GUARANTOR_DEGREE = 5

#: docs/07 §3 — a branch whose guarantees rest on a few shoulders is a
#: portfolio concentration, reported as a note rather than against anyone.
CONCENTRATION_SHARE = 0.40
CONCENTRATION_HOLDERS = 3

MEMBER = "member"
EMPLOYER = "employer"
DOCUMENT = "document"
CONTACT = "contact"


@dataclass(frozen=True, slots=True)
class Cycle:
    """A closed loop of guarantees."""

    members: tuple[str, ...]
    #: Everyone in the loop who has applied at all, oldest first.
    applications: tuple[str, ...] = ()
    #: The most applications falling inside any single window, and the span of
    #: that densest cluster. Measured as a sliding window rather than as the
    #: spread of every application in the loop: docs/07 §3 asks whether three
    #: applications fall within ninety days, and one member who borrowed nine
    #: months earlier must not stretch the measurement past the threshold and
    #: hide four who borrowed in the same fortnight.
    concurrent: int = 0
    window_days: int | None = None

    @property
    def length(self) -> int:
        return len(self.members)

    @property
    def is_hot(self) -> bool:
        """Enough borrowing inside the loop, close enough together, to matter."""
        return self.concurrent >= CYCLE_HIGH_APPLICATIONS

    def as_dict(self) -> dict[str, Any]:
        return {
            "members": list(self.members),
            "length": self.length,
            "applications": list(self.applications),
            "concurrent": self.concurrent,
            "window_days": self.window_days,
            "hot": self.is_hot,
        }


def node_id(kind: str, value: str) -> str:
    return f"{kind}:{value}"


@dataclass
class EntityGraph:
    """Members, what they are attached to, and who stands behind whom."""

    graph: nx.DiGraph = field(default_factory=nx.DiGraph)

    # -- building ----------------------------------------------------------
    def add_member(self, member_id: str, **attributes: Any) -> str:
        node = node_id(MEMBER, member_id)
        self.graph.add_node(node, kind=MEMBER, ref=member_id, **attributes)
        return node

    def add_employment(self, member_id: str, employer_id: str) -> None:
        member = self.add_member(member_id)
        employer = node_id(EMPLOYER, employer_id)
        self.graph.add_node(employer, kind=EMPLOYER, ref=employer_id)
        self.graph.add_edge(member, employer, kind="employed_by")

    def add_guarantee(self, guarantor_member_id: str, borrower_member_id: str, **attributes: Any) -> None:
        """`guarantor` stands behind `borrower`. Direction matters: a cycle in
        an undirected view is just two neighbours guaranteeing each other."""
        source = self.add_member(guarantor_member_id)
        target = self.add_member(borrower_member_id)
        if source != target:
            self.graph.add_edge(source, target, kind="guarantees", **attributes)

    def add_document(self, member_id: str, phash: str, **attributes: Any) -> None:
        member = self.add_member(member_id)
        document = node_id(DOCUMENT, phash)
        self.graph.add_node(document, kind=DOCUMENT, ref=phash)
        self.graph.add_edge(member, document, kind="shares_document", **attributes)

    def add_contact(self, member_id: str, contact: str, **attributes: Any) -> None:
        member = self.add_member(member_id)
        node = node_id(CONTACT, contact)
        self.graph.add_node(node, kind=CONTACT, ref=contact)
        self.graph.add_edge(member, node, kind="shares_contact", **attributes)

    # -- reading -----------------------------------------------------------
    @property
    def guarantees(self) -> nx.DiGraph:
        return self.graph.edge_subgraph(
            [(u, v) for u, v, d in self.graph.edges(data=True) if d.get("kind") == "guarantees"]
        ).copy()

    def members_of(self, nodes: Iterable[str]) -> tuple[str, ...]:
        return tuple(self.graph.nodes[n]["ref"] for n in nodes if self.graph.nodes[n]["kind"] == MEMBER)

    def cycles(
        self,
        *,
        applications: dict[str, date] | None = None,
        minimum: int = MIN_CYCLE,
        maximum: int = MAX_CYCLE,
    ) -> list[Cycle]:
        """Every guarantee loop between `minimum` and `maximum` members long."""
        guarantees = self.guarantees
        if guarantees.number_of_nodes() == 0:
            return []
        found: list[Cycle] = []
        seen: set[frozenset[str]] = set()
        for nodes in nx.simple_cycles(guarantees, length_bound=maximum):
            if len(nodes) < minimum:
                continue
            members = self.members_of(nodes)
            key = frozenset(members)
            if key in seen:
                continue
            seen.add(key)
            found.append(Cycle(members=members, **_application_window(members, applications or {})))
        return sorted(found, key=lambda c: (-c.length, c.members))

    def serial_guarantors(self, *, degree: int = SERIAL_GUARANTOR_DEGREE) -> list[tuple[str, int]]:
        """Members standing behind at least `degree` others."""
        guarantees = self.guarantees
        out = [
            (self.graph.nodes[node]["ref"], int(count))
            for node, count in guarantees.out_degree()
            if count >= degree and self.graph.nodes[node]["kind"] == MEMBER
        ]
        return sorted(out, key=lambda pair: (-pair[1], pair[0]))

    def shared_documents(self) -> list[tuple[str, tuple[str, ...]]]:
        """Documents that more than one member has presented."""
        out: list[tuple[str, tuple[str, ...]]] = []
        for node, data in self.graph.nodes(data=True):
            if data["kind"] != DOCUMENT:
                continue
            holders = self.members_of(self.graph.predecessors(node))
            if len(holders) > 1:
                out.append((str(data["ref"]), tuple(sorted(holders))))
        return sorted(out)

    def shared_contacts(self) -> list[tuple[str, tuple[str, ...]]]:
        out: list[tuple[str, tuple[str, ...]]] = []
        for node, data in self.graph.nodes(data=True):
            if data["kind"] != CONTACT:
                continue
            holders = self.members_of(self.graph.predecessors(node))
            if len(holders) > 1:
                out.append((str(data["ref"]), tuple(sorted(holders))))
        return sorted(out)

    def concentration(self, guarantees_by_branch: dict[str, list[str]]) -> list[dict[str, Any]]:
        """Branches whose guarantees rest on very few people.

        A portfolio observation, not a finding against a member: it says the
        branch is exposed, not that anyone did anything.
        """
        from collections import Counter

        notes: list[dict[str, Any]] = []
        for branch, guarantors in sorted(guarantees_by_branch.items()):
            if not guarantors:
                continue
            counts = Counter(guarantors)
            top = counts.most_common(CONCENTRATION_HOLDERS)
            share = sum(count for _, count in top) / len(guarantors)
            if share >= CONCENTRATION_SHARE and len(counts) > CONCENTRATION_HOLDERS:
                notes.append(
                    {
                        "branch_id": branch,
                        "share": round(share, 4),
                        "holders": [m for m, _ in top],
                        "guarantees": len(guarantors),
                    }
                )
        return notes

    def subgraph_around(self, members: Sequence[str], *, radius: int = 1) -> dict[str, Any]:
        """The neighbourhood a person needs to see to judge a finding."""
        seeds = {node_id(MEMBER, m) for m in members if node_id(MEMBER, m) in self.graph}
        reach: set[str] = set(seeds)
        undirected = self.graph.to_undirected(as_view=True)
        for _ in range(max(radius, 0)):
            for node in list(reach):
                reach.update(undirected.neighbors(node))
        view = self.graph.subgraph(reach)
        return {
            "nodes": [
                {"id": n, "kind": d["kind"], "ref": d["ref"], "seed": n in seeds}
                for n, d in sorted(view.nodes(data=True))
            ],
            "edges": [
                {"source": u, "target": v, "kind": d.get("kind")} for u, v, d in sorted(view.edges(data=True))
            ],
        }


def _application_window(members: tuple[str, ...], applications: dict[str, date]) -> dict[str, Any]:
    """The densest cluster of applications inside the loop.

    A sliding window, not the total spread: the question is whether several
    people in the loop borrowed at about the same time, and a single earlier
    application must not stretch the measurement past the threshold and hide
    them.
    """
    dates = sorted((applications[m], m) for m in members if m in applications)
    if not dates:
        return {"applications": (), "concurrent": 0, "window_days": None}

    best_count, best_span, start = 1, 0, 0
    for end in range(len(dates)):
        while (dates[end][0] - dates[start][0]).days > CYCLE_WINDOW_DAYS:
            start += 1
        count = end - start + 1
        span = (dates[end][0] - dates[start][0]).days
        if count > best_count or (count == best_count and span < best_span):
            best_count, best_span = count, span

    return {
        "applications": tuple(m for _, m in dates),
        "concurrent": best_count,
        "window_days": best_span,
    }
