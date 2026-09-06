"""Scoring how unusual a case is (docs/07 §3).

Advisory by design. The number says a case is unlike the ones the model was
fitted on, which is a reason for a person to look at it sooner, never a reason
to doubt the applicant. The service carries the resulting finding as advisory
so it cannot raise the case level or cost integrity points.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import networkx as nx

from app.facts import CaseBundle
from cio_common.models import add_model_path

add_model_path()

from ml.common.registry import ArtifactError  # noqa: E402
from ml.fraud.features import vector_from  # noqa: E402

__all__ = ["FAMILY", "detector", "score_case", "version_of"]

FAMILY = "fraud"

#: Cycles up to this length count toward "sits inside a loop", matching the
#: bound the graph rules use.
MAX_CYCLE = 8


@lru_cache(maxsize=2)
def detector(version: str | None = None) -> Any:
    from ml.common import registry

    return registry.load(FAMILY, version)["detector"]


def version_of(version: str | None = None) -> str:
    from ml.common import registry

    try:
        return registry.load(FAMILY, version).version
    except (ArtifactError, FileNotFoundError):
        # Fail safe: an unloadable detector is reported as absent. The rules
        # still run, and a case scored without it is visibly distinguishable
        # from one scored with it.
        return "0.0.0-unavailable"


def _graph_statistics(bundle: CaseBundle) -> dict[str, float]:
    """Where this member sits among the guarantees."""
    member_id = bundle.facts.member_id
    guarantees = bundle.graph.guarantees
    node = f"member:{member_id}"
    if node not in guarantees:
        return {"out_degree": 0.0, "in_degree": 0.0, "neighbourhood": 0.0, "in_cycle": 0.0}

    inside = any(node in cycle for cycle in nx.simple_cycles(guarantees, length_bound=MAX_CYCLE))
    undirected = guarantees.to_undirected(as_view=True)
    reach = {node}
    for _ in range(3):
        reach |= {n for current in list(reach) for n in undirected.neighbors(current)}
    return {
        "out_degree": float(guarantees.out_degree(node)),
        "in_degree": float(guarantees.in_degree(node)),
        "neighbourhood": float(len(reach) - 1),
        "in_cycle": 1.0 if inside else 0.0,
    }


def _document_statistics(bundle: CaseBundle) -> dict[str, float]:
    """What the case file looks like, from what the document service reported."""
    findings = bundle.facts.document_findings
    documents = {str(f.get("document_id")) for f in findings if f.get("document_id")}
    return {
        "count": float(len(documents)),
        "distinct_types": 0.0,
        "missing_required": 0.0,
        "clean_digital_share": 0.0,
    }


def score_case(bundle: CaseBundle, *, version: str | None = None) -> float | None:
    """How unusual this case is, in [0, 1], or None if no detector is loaded."""
    try:
        model = detector(version)
    except (ArtifactError, FileNotFoundError):
        return None

    vector = vector_from(
        {
            "income_source_variance": None,
            "application_count_12m": len(bundle.facts.recent_applications) or None,
            "contact_change_days": _contact_days(bundle),
            "doc_min_conf": None,
            "findings_max_severity": _worst_document_severity(bundle),
        },
        documents=_document_statistics(bundle),
        graph=_graph_statistics(bundle),
        application={},
    )
    return float(model.score_one(vector.as_row()))


def _contact_days(bundle: CaseBundle) -> float | None:
    facts = bundle.facts
    if facts.applied_at is None or facts.contact_updated_at is None:
        return None
    return float((facts.applied_at - facts.contact_updated_at).days)


def _worst_document_severity(bundle: CaseBundle) -> float | None:
    order = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    scores = [order.get(str(f.get("severity", "")).upper(), 0) for f in bundle.facts.document_findings]
    return float(max(scores)) if scores else None
