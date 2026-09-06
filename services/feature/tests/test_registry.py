"""T-030 — the feature registry (docs/07 §2.1)."""

from __future__ import annotations

import pytest

from app.registry import (
    FEATURES,
    REGISTRY_VERSION,
    FeatureFamily,
    PermittedUse,
    definition,
    families,
    features_for,
    names,
)

#: docs/07 §2.1 lists these by name.
DOCUMENTED = {
    "ontime_rate_24m",
    "arrears_events_12m",
    "months_since_last_arrears",
    "restructures_36m",
    "facilities_open",
    "facilities_new_6m",
    "utilisation",
    "tenure_months",
    "savings_balance",
    "savings_slope_180d",
    "savings_paused_months",
    "share_capital_units",
    "share_capital_ratio",
    "income_verified_monthly",
    "income_source_variance",
    "dsr_proposed",
    "commitments_monthly",
    "employer_sector",
    "employer_tenure_months",
    "application_count_12m",
    "contact_change_days",
    "doc_min_conf",
    "findings_max_severity",
}


def test_every_documented_feature_is_declared() -> None:
    assert set(names()) == DOCUMENTED


def test_feature_names_are_unique() -> None:
    assert len(names()) == len(set(names()))


def test_every_feature_declares_what_it_means_and_where_it_comes_from() -> None:
    for feature in FEATURES:
        assert feature.description.endswith("."), f"{feature.name} has no sentence"
        assert "." in feature.source, f"{feature.name} names no source table"
        assert feature.permitted_uses, f"{feature.name} declares no purpose"


def test_every_feature_belongs_to_a_decision_factor() -> None:
    """A feature that informs nothing has no business in a snapshot."""
    for feature in FEATURES:
        assert isinstance(feature.family, FeatureFamily)


def test_no_feature_carries_a_protected_characteristic() -> None:
    """CLAUDE.md §5 — and docs/12 §5 makes this a structural check."""
    forbidden = (
        "ethnic",
        "religio",
        "gender",
        "sex",
        "health",
        "disabil",
        "political",
        "marital",
        "race",
        "nationality",
    )
    for feature in FEATURES:
        blob = f"{feature.name} {feature.description}".casefold()
        hits = [word for word in forbidden if word in blob]
        assert not hits, f"{feature.name} mentions {hits}"


def test_the_purpose_filter_narrows_the_set() -> None:
    """docs/03 §2 — a collections call must not see underwriting-only inputs."""
    underwriting = {f.name for f in features_for(PermittedUse.UNDERWRITING)}
    collections = {f.name for f in features_for(PermittedUse.COLLECTIONS)}
    fraud = {f.name for f in features_for(PermittedUse.FRAUD)}

    assert collections < underwriting, "collections should see fewer features"
    assert "dsr_proposed" in underwriting
    assert "dsr_proposed" not in collections
    assert "findings_max_severity" in fraud


def test_monotone_directions_are_sane() -> None:
    """A worse input must never be allowed to improve a score (docs/07 §2.2)."""
    assert definition("arrears_events_12m").monotone == 1
    assert definition("ontime_rate_24m").monotone == -1
    assert definition("dsr_proposed").monotone == 1
    assert definition("savings_balance").monotone == -1
    for feature in FEATURES:
        assert feature.monotone in (-1, 0, 1)


def test_windowed_features_state_their_window() -> None:
    for feature in FEATURES:
        if feature.name.endswith(("_12m", "_24m", "_36m", "_6m", "_180d")):
            assert feature.window_days, f"{feature.name} names no window"


def test_families_partition_the_registry() -> None:
    grouped = families()
    assert sum(len(items) for items in grouped.values()) == len(FEATURES)


def test_an_unknown_feature_is_refused() -> None:
    with pytest.raises(KeyError, match="unknown feature"):
        definition("wizardry")


def test_the_registry_is_versioned() -> None:
    assert REGISTRY_VERSION.startswith("features/")
