"""The case vector the anomaly detector scores (docs/07 §3).

docs/07 §3 asks for an isolation forest over origination features and document
statistics. Two constraints shape what that can mean here.

The feature registry scopes what the fraud service may read: five of the
twenty-three features carry `FRAUD` in their permitted uses, and least
privilege is a governance control rather than a preference, so the model sees
those five and not the rest.

The graph is the other half. A case that looks ordinary in isolation can sit
in an unusual position: guaranteeing many people, sitting inside a loop, or
attached to nobody at all. Those are statistics about the case, and they are
exactly what a rule threshold cannot express.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any

__all__ = ["FEATURE_NAMES", "FRAUD_FEATURES", "CaseVector", "vector_from"]

#: The registry features whose permitted uses include FRAUD (docs/07 §2.1).
FRAUD_FEATURES = (
    "income_source_variance",
    "application_count_12m",
    "contact_change_days",
    "doc_min_conf",
    "findings_max_severity",
)


@dataclass(frozen=True, slots=True)
class CaseVector:
    """One case, as numbers the forest can compare."""

    # -- what the feature registry lets fraud see -------------------------
    income_source_variance: float = 0.0
    application_count_12m: float = 0.0
    contact_change_days: float = 3650.0
    doc_min_conf: float = 1.0
    findings_max_severity: float = 0.0
    # -- the case file ----------------------------------------------------
    document_count: float = 0.0
    document_types: float = 0.0
    missing_required: float = 0.0
    clean_digital_share: float = 0.0
    # -- where the member sits in the graph -------------------------------
    guarantees_given: float = 0.0
    guarantees_received: float = 0.0
    neighbourhood_size: float = 0.0
    in_cycle: float = 0.0
    # -- the size of what is being asked for -------------------------------
    amount_to_salary: float = 0.0
    tenor_months: float = 0.0

    def as_row(self) -> dict[str, float]:
        return {k: float(v) for k, v in asdict(self).items()}


FEATURE_NAMES: tuple[str, ...] = tuple(f.name for f in fields(CaseVector))


def vector_from(
    features: dict[str, Any] | None = None,
    *,
    documents: dict[str, Any] | None = None,
    graph: dict[str, Any] | None = None,
    application: dict[str, Any] | None = None,
) -> CaseVector:
    """Assemble a case vector from whatever the caller could gather.

    A missing part is filled with the value that says "nothing unusual here",
    never with a zero that would read as an extreme. An absent contact-change
    date means the contact has not changed recently, so it becomes a long
    interval rather than none at all.
    """
    features = features or {}
    documents = documents or {}
    graph = graph or {}
    application = application or {}

    defaults = CaseVector()

    def number(source: dict[str, Any], key: str, fallback: float) -> float:
        value = source.get(key)
        if value is None:
            return fallback
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    return CaseVector(
        income_source_variance=number(features, "income_source_variance", defaults.income_source_variance),
        application_count_12m=number(features, "application_count_12m", defaults.application_count_12m),
        contact_change_days=number(features, "contact_change_days", defaults.contact_change_days),
        doc_min_conf=number(features, "doc_min_conf", defaults.doc_min_conf),
        findings_max_severity=number(features, "findings_max_severity", defaults.findings_max_severity),
        document_count=number(documents, "count", 0.0),
        document_types=number(documents, "distinct_types", 0.0),
        missing_required=number(documents, "missing_required", 0.0),
        clean_digital_share=number(documents, "clean_digital_share", 0.0),
        guarantees_given=number(graph, "out_degree", 0.0),
        guarantees_received=number(graph, "in_degree", 0.0),
        neighbourhood_size=number(graph, "neighbourhood", 0.0),
        in_cycle=number(graph, "in_cycle", 0.0),
        amount_to_salary=number(application, "amount_to_salary", 0.0),
        tenor_months=number(application, "tenor_months", 0.0),
    )
