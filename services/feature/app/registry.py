"""The feature registry (docs/07 §2.1).

Every feature is declared here before it can be computed: what it means, the
window it looks over, where it comes from, and which purposes it may be used
for. A feature with no declaration cannot enter a snapshot, which is what stops
a model quietly learning from something nobody agreed to.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "FEATURES",
    "REGISTRY_VERSION",
    "FeatureDef",
    "FeatureFamily",
    "PermittedUse",
    "families",
    "feature",
    "features_for",
    "names",
]

#: Bump when a definition changes meaning. Snapshots record it.
REGISTRY_VERSION = "features/1.0"


class FeatureFamily(StrEnum):
    """Which Decision Factor a feature informs (docs/05 §4)."""

    CONDUCT = "CONDUCT"
    CAPACITY = "CAPACITY"
    COMMITMENT = "COMMITMENT"
    CONDITIONS = "CONDITIONS"
    INTEGRITY = "INTEGRITY"


class PermittedUse(StrEnum):
    UNDERWRITING = "UNDERWRITING"
    SERVICING = "SERVICING"
    COLLECTIONS = "COLLECTIONS"
    FRAUD = "FRAUD"
    ANALYTICS = "ANALYTICS"


@dataclass(frozen=True, slots=True)
class FeatureDef:
    name: str
    family: FeatureFamily
    description: str
    source: str
    #: Days the feature looks back over. None means the whole history.
    window_days: int | None = None
    permitted_uses: tuple[PermittedUse, ...] = (PermittedUse.UNDERWRITING,)
    dtype: str = "float"
    version: str = "1.0"
    #: Direction a model is allowed to learn: +1 rising is worse, -1 is better,
    #: 0 unconstrained. Feeds the monotone constraints in T-031.
    monotone: int = 0

    def permits(self, use: PermittedUse) -> bool:
        return use in self.permitted_uses

    def as_row(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "family": str(self.family),
            "description": self.description,
            "source": self.source,
            "window_days": self.window_days,
            "permitted_uses": [str(u) for u in self.permitted_uses],
            "dtype": self.dtype,
            "version": self.version,
            "monotone": self.monotone,
        }


_ALL = PermittedUse.UNDERWRITING, PermittedUse.SERVICING, PermittedUse.COLLECTIONS
_UW = (PermittedUse.UNDERWRITING,)
_UW_FRAUD = PermittedUse.UNDERWRITING, PermittedUse.FRAUD


def feature(**kwargs: Any) -> FeatureDef:
    return FeatureDef(**kwargs)


#: docs/07 §2.1, in the order the document lists them.
FEATURES: tuple[FeatureDef, ...] = (
    # --- conduct: what the member has actually done ------------------------
    feature(
        name="ontime_rate_24m",
        family=FeatureFamily.CONDUCT,
        window_days=730,
        description="Share of due events paid on or before the due date.",
        source="app_member.member_event",
        permitted_uses=_ALL,
        monotone=-1,
    ),
    feature(
        name="arrears_events_12m",
        family=FeatureFamily.CONDUCT,
        window_days=365,
        description="Payments more than a week late in the last year.",
        source="app_member.member_event",
        permitted_uses=_ALL,
        monotone=1,
    ),
    feature(
        name="months_since_last_arrears",
        family=FeatureFamily.CONDUCT,
        description="Months since the most recent late payment.",
        source="app_member.member_event",
        permitted_uses=_ALL,
        monotone=-1,
    ),
    feature(
        name="restructures_36m",
        family=FeatureFamily.CONDUCT,
        window_days=1095,
        description="Arrangements started in the last three years.",
        source="core.arrangement",
        permitted_uses=_ALL,
        monotone=1,
    ),
    feature(
        name="facilities_open",
        family=FeatureFamily.CONDUCT,
        description="Active financing accounts.",
        source="core.account",
        permitted_uses=_ALL,
        dtype="int",
        monotone=1,
    ),
    feature(
        name="facilities_new_6m",
        family=FeatureFamily.CONDUCT,
        window_days=183,
        description="Accounts opened in the last six months.",
        source="core.account",
        permitted_uses=_ALL,
        dtype="int",
        monotone=1,
    ),
    feature(
        name="utilisation",
        family=FeatureFamily.CONDUCT,
        description="Total exposure against the member's grade limit.",
        source="core.account",
        permitted_uses=_UW,
        monotone=1,
    ),
    # --- commitment: the member's standing with the cooperative -----------
    feature(
        name="tenure_months",
        family=FeatureFamily.COMMITMENT,
        description="Months since the member joined.",
        source="core.member",
        permitted_uses=_ALL,
        dtype="int",
        monotone=-1,
    ),
    feature(
        name="savings_balance",
        family=FeatureFamily.COMMITMENT,
        description="Most recent savings balance.",
        source="app_member.member_event",
        permitted_uses=_ALL,
        monotone=-1,
    ),
    feature(
        name="savings_slope_180d",
        family=FeatureFamily.COMMITMENT,
        window_days=180,
        description="Trend in the savings balance over six months.",
        source="app_member.member_event",
        permitted_uses=_ALL,
        monotone=-1,
    ),
    feature(
        name="savings_paused_months",
        family=FeatureFamily.COMMITMENT,
        description="Months in which no savings deposit was made.",
        source="app_member.member_event",
        permitted_uses=_ALL,
        dtype="int",
        monotone=1,
    ),
    feature(
        name="share_capital_units",
        family=FeatureFamily.COMMITMENT,
        description="Share capital units held.",
        source="app_member.member_event",
        permitted_uses=_ALL,
        dtype="int",
        monotone=-1,
    ),
    feature(
        name="share_capital_ratio",
        family=FeatureFamily.COMMITMENT,
        description="Share capital against the product minimum.",
        source="app_member.member_event",
        permitted_uses=_UW,
        monotone=-1,
    ),
    # --- capacity: what the member can afford ------------------------------
    feature(
        name="income_verified_monthly",
        family=FeatureFamily.CAPACITY,
        description="Verified monthly income.",
        source="core.deduction",
        permitted_uses=_UW,
        monotone=-1,
    ),
    feature(
        name="income_source_variance",
        family=FeatureFamily.CAPACITY,
        description="Disagreement between the sources reporting income.",
        source="app_document.extraction",
        permitted_uses=_UW_FRAUD,
        monotone=1,
    ),
    feature(
        name="dsr_proposed",
        family=FeatureFamily.CAPACITY,
        description="Debt service ratio including the requested instalment.",
        source="policy.affordability",
        permitted_uses=_UW,
        monotone=1,
    ),
    feature(
        name="commitments_monthly",
        family=FeatureFamily.CAPACITY,
        description="Existing monthly repayment commitments.",
        source="core.account",
        permitted_uses=_UW,
        monotone=1,
    ),
    # --- conditions: the world the member sits in -------------------------
    feature(
        name="employer_sector",
        family=FeatureFamily.CONDITIONS,
        description="Sector of the member's employer.",
        source="core.employer",
        permitted_uses=_UW,
        dtype="category",
    ),
    feature(
        name="employer_tenure_months",
        family=FeatureFamily.CONDITIONS,
        description="Months of salary deductions from this employer.",
        source="app_member.member_event",
        permitted_uses=_UW,
        dtype="int",
        monotone=-1,
    ),
    # --- integrity: whether the file is what it appears to be -------------
    feature(
        name="application_count_12m",
        family=FeatureFamily.INTEGRITY,
        window_days=365,
        description="Applications made in the last year.",
        source="core.application_ext",
        permitted_uses=_UW_FRAUD,
        dtype="int",
        monotone=1,
    ),
    feature(
        name="contact_change_days",
        family=FeatureFamily.INTEGRITY,
        description="Days since the member's contact details last changed.",
        source="core.member",
        permitted_uses=_UW_FRAUD,
        monotone=-1,
    ),
    feature(
        name="doc_min_conf",
        family=FeatureFamily.INTEGRITY,
        description="Lowest confidence among the critical document fields.",
        source="app_document.extraction",
        permitted_uses=_UW_FRAUD,
        monotone=-1,
    ),
    feature(
        name="findings_max_severity",
        family=FeatureFamily.INTEGRITY,
        description="Worst open document finding, as an ordinal.",
        source="app_document.finding",
        permitted_uses=_UW_FRAUD,
        dtype="int",
        monotone=1,
    ),
)

_BY_NAME = {definition.name: definition for definition in FEATURES}


def names() -> tuple[str, ...]:
    return tuple(definition.name for definition in FEATURES)


def families() -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for definition in FEATURES:
        grouped.setdefault(str(definition.family), []).append(definition.name)
    return {family: tuple(items) for family, items in sorted(grouped.items())}


def features_for(use: PermittedUse) -> tuple[FeatureDef, ...]:
    """Only the features this purpose may see (docs/03 §2)."""
    return tuple(d for d in FEATURES if d.permits(use))


def definition(name: str) -> FeatureDef:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise KeyError(f"unknown feature {name!r}; declared: {', '.join(names())}") from None
