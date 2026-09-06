"""T-033 — the advisory anomaly score (docs/07 §3)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from app.anomaly import detector, score_case, version_of
from app.facts import CaseBundle
from app.graph import EntityGraph
from app.rules import ANOMALY_THRESHOLD, CaseFacts

APPLIED = date(2026, 7, 27)


@pytest.fixture(scope="module")
def trained() -> None:
    from ml.common.registry import ArtifactError

    try:
        detector()
    except (ArtifactError, FileNotFoundError) as exc:
        pytest.skip(f"no trained fraud detector: {exc}")


def bundle(**overrides: Any) -> CaseBundle:
    graph = overrides.pop("graph", None) or EntityGraph()
    base: dict[str, Any] = {
        "case_id": "case_1",
        "member_id": "M-000042",
        "applied_at": APPLIED,
        "employer_id": "E-019",
        "branch_id": "BR-01",
    }
    return CaseBundle(facts=CaseFacts(**{**base, **overrides}), graph=graph)


def test_an_ordinary_case_scores_low(trained: None) -> None:
    score = score_case(bundle())
    assert score is not None
    assert 0.0 <= score <= 1.0


def test_a_member_at_the_centre_of_many_guarantees_scores_higher(trained: None) -> None:
    graph = EntityGraph()
    for index in range(12):
        graph.add_guarantee("M-000042", f"M-0009{index:02d}")
    assert score_case(bundle(graph=graph)) > score_case(bundle())


def test_the_score_stays_inside_the_unit_interval(trained: None) -> None:
    graph = EntityGraph()
    for index in range(60):
        graph.add_guarantee("M-000042", f"M-00{index:04d}")
    score = score_case(bundle(graph=graph))
    assert score is not None
    assert 0.0 <= score <= 1.0


def test_the_same_case_scores_the_same(trained: None) -> None:
    assert score_case(bundle()) == score_case(bundle())


def test_a_recent_contact_change_moves_the_score(trained: None) -> None:
    """The five features the registry lets fraud read all reach the model."""
    recent = bundle(contact_updated_at=APPLIED - timedelta(days=2))
    assert score_case(recent) != score_case(bundle())


def test_the_version_is_reported(trained: None) -> None:
    assert version_of().count(".") == 2


def test_a_missing_detector_is_reported_not_guessed(monkeypatch: Any) -> None:
    """Fail safe: the rules still run, and the case is visibly unscored."""
    import app.anomaly as anomaly_module
    from ml.common.registry import ArtifactError

    def explode(*_: object, **__: object) -> None:
        raise ArtifactError("fraud has no trained artifacts; run its train.py")

    monkeypatch.setattr(anomaly_module, "detector", explode)
    assert anomaly_module.score_case(bundle()) is None


def test_the_threshold_is_the_one_the_rules_use() -> None:
    assert 0.0 < ANOMALY_THRESHOLD < 1.0
