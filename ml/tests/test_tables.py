"""T-031 — the two tables that govern the model, and their agreement with
the rest of the platform."""

from __future__ import annotations

from pathlib import Path

import yaml

from ml.credit_risk.explain import monotone_table, reason_map

ROOT = Path(__file__).resolve().parents[2]


def _feature_registry() -> dict[str, int]:
    import sys

    service = ROOT / "services" / "feature"
    if str(service) not in sys.path:
        sys.path.insert(0, str(service))
    from app.registry import FEATURES

    return {f.name: f.monotone for f in FEATURES}


def _approved_codes() -> set[str]:
    loaded = yaml.safe_load((ROOT / "contracts" / "reason_codes.yaml").read_text())
    return {code for group in loaded["groups"].values() for code in group["codes"]}


def test_the_direction_table_matches_the_feature_registry() -> None:
    """Two files declare the same policy, so they must not drift apart."""
    assert monotone_table() == _feature_registry()


def test_every_direction_is_a_real_direction() -> None:
    assert set(monotone_table().values()) <= {-1, 0, 1}


def test_every_feature_has_a_reason_mapping() -> None:
    assert set(reason_map()) == set(_feature_registry())


def test_every_reason_code_is_approved() -> None:
    """A member must never be told something the vocabulary has not sanctioned."""
    approved = _approved_codes()
    used = {code for entry in reason_map().values() for code in entry.values()}
    assert used <= approved, used - approved


def test_reason_mappings_only_use_the_two_directions() -> None:
    for feature, entry in reason_map().items():
        assert set(entry) <= {"adverse", "favourable"}, feature


def test_an_adverse_reason_exists_for_every_constrained_feature() -> None:
    """If a feature can push risk up, there must be words for saying so."""
    directions = monotone_table()
    for feature, entry in reason_map().items():
        if directions[feature] != 0 and "adverse" not in entry:
            # Only a favourable-only mapping is allowed, and only where the
            # feature can genuinely never count against an applicant.
            assert feature in {"share_capital_units", "share_capital_ratio"}, feature


def test_the_tables_are_versioned() -> None:
    for name in ("monotone.yaml", "reason_map.yaml"):
        loaded = yaml.safe_load((ROOT / "ml" / "credit_risk" / name).read_text())
        assert loaded["schema"].endswith("/1.0")
        assert loaded["version"]
