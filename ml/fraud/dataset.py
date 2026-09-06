"""Building the fraud training frame (docs/07 §3).

One row per application: what the fraud service may read about the member,
what its case file looks like, and where the member sits in the guarantee
graph. Assembled from the generated corpus and the core record, because the
document pipeline is only populated for cases that have actually been through
it and an anomaly detector needs the whole queue.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import networkx as nx
import pandas as pd

from ml.fraud.features import FEATURE_NAMES, vector_from

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = ROOT / "synthetic" / "out" / "documents"

#: docs/10 §7 — a complete bundle carries these; a case missing one of them is
#: incomplete rather than suspicious, which is what the count records.
REQUIRED_TYPES = ("IDENTITY", "PAYSLIP_LATEST_3", "EMPLOYMENT_CONFIRMATION")

#: How far the guarantee walk goes when counting a member's neighbourhood.
GRAPH_HOPS = 3

#: Cycles up to this length count as "in a cycle" (docs/07 §3).
MAX_CYCLE = 8


@dataclass(frozen=True, slots=True)
class Frame:
    rows: pd.DataFrame
    feature_names: list[str]

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "applications": len(self.rows),
            "features": len(self.feature_names),
            "in_cycle": int(self.rows["in_cycle"].sum()),
            "with_documents": int((self.rows["document_count"] > 0).sum()),
            "incomplete_bundles": int((self.rows["missing_required"] > 0).sum()),
        }


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _guarantee_graph(population: Path) -> nx.DiGraph:
    """Who stands behind whom, from the generated guarantees."""
    accounts = {row["account_id"]: str(row["member_id"]) for row in _read(population / "account.jsonl")}
    graph = nx.DiGraph()
    for row in _read(population / "guarantor.jsonl"):
        borrower = accounts.get(row["account_id"])
        guarantor = str(row["guarantor_member_id"])
        if borrower and borrower != guarantor:
            graph.add_edge(guarantor, borrower)
    return graph


def _cycle_members(graph: nx.DiGraph) -> set[str]:
    """Everyone sitting inside a guarantee loop short enough to matter."""
    inside: set[str] = set()
    for nodes in nx.simple_cycles(graph, length_bound=MAX_CYCLE):
        inside.update(nodes)
    return inside


def _income_variance(population: Path) -> dict[str, float]:
    """How much a member's reported net pay moves between cycles.

    A steady salary varies by almost nothing; income that jumps around is
    harder to verify, which is what the feature is for.
    """
    by_member: dict[str, list[float]] = defaultdict(list)
    for row in _read(population / "deduction.jsonl"):
        if row.get("net_salary") is not None:
            by_member[str(row["member_id"])].append(float(row["net_salary"]))
    out: dict[str, float] = {}
    for member_id, nets in by_member.items():
        if len(nets) < 3:
            continue
        mean = sum(nets) / len(nets)
        if mean <= 0:
            continue
        spread = (sum((n - mean) ** 2 for n in nets) / len(nets)) ** 0.5
        out[member_id] = round(spread / mean, 5)
    return out


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def build(corpus: Path = DEFAULT_CORPUS, *, population: Path | None = None) -> Frame:
    population = population or corpus.parent
    applications = _read(corpus / "applications.jsonl")
    if not applications:
        raise FileNotFoundError(f"no applications under {corpus}")

    members = {row["member_id"]: row for row in _read(population / "member.jsonl")}
    income_variance = _income_variance(population)
    applications_by_member: dict[str, list[date]] = defaultdict(list)
    for row in applications:
        applications_by_member[str(row["member_id"])].append(date.fromisoformat(str(row["created_at"])))
    documents_by_application: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for document in _read(corpus / "documents.jsonl"):
        documents_by_application[str(document["application_id"])].append(document)

    graph = _guarantee_graph(population)
    in_cycle = _cycle_members(graph)
    undirected = graph.to_undirected(as_view=True)

    rows: list[dict[str, Any]] = []
    for application in applications:
        member_id = str(application["member_id"])
        member = members.get(member_id, {})
        documents = documents_by_application.get(str(application["application_id"]), [])
        present = {d["type"] for d in documents}

        salary = float(member.get("salary_monthly") or 0.0)
        amount = float(application.get("amount") or 0.0)

        neighbourhood = 0
        if member_id in undirected:
            reach = {member_id}
            for _ in range(GRAPH_HOPS):
                reach |= {n for node in list(reach) for n in undirected.neighbors(node)}
            neighbourhood = len(reach) - 1

        applied_at = date.fromisoformat(str(application["created_at"]))
        changed = _as_date(member.get("contact_updated_at"))
        vector = vector_from(
            {
                "income_source_variance": income_variance.get(member_id),
                "application_count_12m": sum(
                    1 for when in applications_by_member[member_id] if 0 <= (applied_at - when).days <= 365
                ),
                "contact_change_days": ((applied_at - changed).days if changed else None),
                # Extraction confidence and open findings exist only once a
                # case has been through the document pipeline. They are left
                # at their neutral defaults here and the detector drops any
                # column that never varies, so the model is not fitted on a
                # column that was simply never populated.
                "doc_min_conf": None,
                "findings_max_severity": None,
            },
            documents={
                "count": len(documents),
                "distinct_types": len(present),
                "missing_required": sum(1 for t in REQUIRED_TYPES if t not in present),
                "clean_digital_share": (
                    sum(1 for d in documents if d.get("clean_digital")) / len(documents) if documents else 0.0
                ),
            },
            graph={
                "out_degree": graph.out_degree(member_id) if member_id in graph else 0,
                "in_degree": graph.in_degree(member_id) if member_id in graph else 0,
                "neighbourhood": neighbourhood,
                "in_cycle": 1.0 if member_id in in_cycle else 0.0,
            },
            application={
                "amount_to_salary": amount / salary if salary > 0 else 0.0,
                "tenor_months": application.get("tenor_months") or 0,
            },
        )
        rows.append(
            {"application_id": application["application_id"], "member_id": member_id, **vector.as_row()}
        )

    return Frame(rows=pd.DataFrame.from_records(rows), feature_names=list(FEATURE_NAMES))
