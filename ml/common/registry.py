"""Loading and saving model artifacts (docs/07 §2.2).

A decision cites a `model_run_id`, and that run must stay reconstructable years
later. So an artifact directory is written once under its version and never
edited: retraining produces a new version beside the old one. `latest.txt`
names the version a service loads by default, which makes a rollback a
one-line change rather than a rebuild.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib

ROOT = Path(__file__).resolve().parents[1]

#: `<family>/<yyyy.mm.n>` — sortable, and it says when the model was built.
VERSION = re.compile(r"^\d{4}\.\d{2}\.\d+$")

_POINTER = "latest.txt"


class ArtifactError(RuntimeError):
    """The artifact store does not hold what was asked for."""


def artifacts_root(family: str) -> Path:
    return ROOT / family / "artifacts"


def next_version(family: str, *, today: datetime | None = None) -> str:
    """The next free version for this month."""
    stamp = (today or datetime.now(UTC)).strftime("%Y.%m")
    root = artifacts_root(family)
    used = [
        int(path.name.rsplit(".", 1)[1])
        for path in root.glob(f"{stamp}.*")
        if path.is_dir() and VERSION.match(path.name)
    ]
    return f"{stamp}.{max(used, default=0) + 1}"


def versions(family: str) -> list[str]:
    root = artifacts_root(family)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and VERSION.match(p.name))


def latest_version(family: str) -> str:
    """The version a service loads when none is named."""
    pointer = artifacts_root(family) / _POINTER
    if pointer.is_file():
        named = pointer.read_text().strip()
        if named in versions(family):
            return named
        raise ArtifactError(f"{family} latest points at missing version {named!r}")
    if found := versions(family):
        return found[-1]
    raise ArtifactError(f"{family} has no trained artifacts; run its train.py")


def set_latest(family: str, version: str) -> None:
    """Point serving at a version. This is the rollback switch."""
    if version not in versions(family):
        raise ArtifactError(f"{family} has no version {version!r}")
    (artifacts_root(family) / _POINTER).write_text(f"{version}\n")


@dataclass(frozen=True, slots=True)
class Artifacts:
    """One trained version, loaded."""

    family: str
    version: str
    path: Path
    objects: dict[str, Any]
    metrics: dict[str, Any]

    def __getitem__(self, name: str) -> Any:
        try:
            return self.objects[name]
        except KeyError as exc:
            raise ArtifactError(f"{self.family}/{self.version} has no {name!r}") from exc

    def get(self, name: str, default: Any = None) -> Any:
        return self.objects.get(name, default)


def save(
    family: str,
    version: str,
    *,
    objects: dict[str, Any],
    metrics: dict[str, Any],
    card: str,
) -> Path:
    """Write a version. Refuses to overwrite one that already exists."""
    if not VERSION.match(version):
        raise ArtifactError(f"{version!r} is not a <yyyy.mm.n> version")
    path = artifacts_root(family) / version
    if path.exists():
        raise ArtifactError(f"{family}/{version} already exists; artifacts are write-once")
    path.mkdir(parents=True)
    for name, obj in objects.items():
        joblib.dump(obj, path / f"{name}.joblib")
    (path / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str) + "\n")
    (path / "card.md").write_text(card)
    return path


def load(family: str, version: str | None = None) -> Artifacts:
    """Load a version, or the one `latest.txt` names."""
    version = version or latest_version(family)
    path = artifacts_root(family) / version
    if not path.is_dir():
        raise ArtifactError(f"{family}/{version} is not in the artifact store")
    objects = {file.stem: joblib.load(file) for file in sorted(path.glob("*.joblib"))}
    metrics_file = path / "metrics.json"
    metrics = json.loads(metrics_file.read_text()) if metrics_file.is_file() else {}
    return Artifacts(family=family, version=version, path=path, objects=objects, metrics=metrics)
