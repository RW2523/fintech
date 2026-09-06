"""T-031 — the artifact store (docs/07 §2.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ml.common import registry


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(registry, "ROOT", tmp_path)
    return tmp_path


def _save(version: str, value: object = 1) -> Path:
    return registry.save("demo", version, objects={"thing": value}, metrics={"auc": 0.8}, card="# card\n")


def test_a_version_is_written_once(store: Path) -> None:
    """A decision cites a version, so that version must never change under it."""
    _save("2026.09.1")
    with pytest.raises(registry.ArtifactError, match="write-once"):
        _save("2026.09.1", value=2)


def test_a_saved_version_loads_back(store: Path) -> None:
    _save("2026.09.1", value={"weights": [1, 2, 3]})
    loaded = registry.load("demo", "2026.09.1")
    assert loaded["thing"] == {"weights": [1, 2, 3]}
    assert loaded.metrics == {"auc": 0.8}
    assert (loaded.path / "card.md").read_text() == "# card\n"


def test_versions_are_listed_in_order(store: Path) -> None:
    for version in ("2026.09.2", "2026.08.1", "2026.09.10"):
        _save(version)
    assert registry.versions("demo") == ["2026.08.1", "2026.09.10", "2026.09.2"]


def test_the_next_version_follows_the_last_this_month(store: Path) -> None:
    from datetime import UTC, datetime

    today = datetime(2026, 9, 6, tzinfo=UTC)
    assert registry.next_version("demo", today=today) == "2026.09.1"
    _save("2026.09.1")
    assert registry.next_version("demo", today=today) == "2026.09.2"


def test_a_malformed_version_is_refused(store: Path) -> None:
    with pytest.raises(registry.ArtifactError, match="not a"):
        _save("v1")


def test_serving_follows_the_pointer(store: Path) -> None:
    _save("2026.09.1")
    _save("2026.09.2")
    registry.set_latest("demo", "2026.09.1")
    assert registry.latest_version("demo") == "2026.09.1"
    assert registry.load("demo").version == "2026.09.1"


def test_rolling_back_is_one_line(store: Path) -> None:
    _save("2026.09.1", value="old")
    _save("2026.09.2", value="new")
    registry.set_latest("demo", "2026.09.2")
    assert registry.load("demo")["thing"] == "new"
    registry.set_latest("demo", "2026.09.1")
    assert registry.load("demo")["thing"] == "old"


def test_pointing_at_a_missing_version_is_refused(store: Path) -> None:
    _save("2026.09.1")
    with pytest.raises(registry.ArtifactError, match="no version"):
        registry.set_latest("demo", "2026.09.9")


def test_a_dangling_pointer_is_reported_not_ignored(store: Path) -> None:
    _save("2026.09.1")
    registry.set_latest("demo", "2026.09.1")
    (registry.artifacts_root("demo") / "latest.txt").write_text("2026.09.9\n")
    with pytest.raises(registry.ArtifactError, match="missing version"):
        registry.latest_version("demo")


def test_an_untrained_family_says_so(store: Path) -> None:
    with pytest.raises(registry.ArtifactError, match=r"run its train\.py"):
        registry.latest_version("nothing_here")


def test_metrics_are_written_as_readable_json(store: Path) -> None:
    path = _save("2026.09.1")
    assert json.loads((path / "metrics.json").read_text()) == {"auc": 0.8}
