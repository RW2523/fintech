"""The advisory anomaly detector (docs/07 §4.3).

An Isolation Forest over the whole feature vector, scoring how unlike the book
each member looks. Advisory means advisory: it never raises a state change on
its own, because nobody can say what it objected to.

It earns its place by catching the combination nobody wrote a rule for. A
member whose payments are fine, whose deduction is fine and whose savings are
fine but whose pattern across all three is unlike anybody else's is worth a
look, and no single-signal detector will ever find them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["MIN_MEMBERS", "AnomalyScores", "score_book"]

#: A member cannot be judged unusual against a book that has not been seen.
#: Below this the detector reports nothing rather than a score computed from
#: too few neighbours to mean anything.
MIN_MEMBERS = 200


@dataclass
class AnomalyScores:
    """What the forest thought, and which features it was given."""

    scores: dict[str, float] = field(default_factory=dict)
    columns: list[str] = field(default_factory=list)
    members: int = 0
    available: bool = True
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "members": self.members,
            "columns": self.columns,
            "flagged": sum(1 for value in self.scores.values() if value > 0.5),
        }


def _matrix(rows: list[dict[str, Any]]) -> tuple[list[str], list[list[float]], list[str]]:
    """A dense matrix from sparse feature dicts.

    Only columns present for every member are used. A feature some members do
    not have would otherwise be imputed, and an imputed value is a made-up
    observation that the forest cannot tell from a real one.
    """
    shared: set[str] | None = None
    for row in rows:
        keys = {
            name for name, value in (row.get("features") or {}).items() if isinstance(value, (int, float))
        }
        shared = keys if shared is None else (shared & keys)
    columns = sorted(shared or set())

    members = [str(row["member_id"]) for row in rows]
    matrix = [[float(row["features"][name]) for name in columns] for row in rows]
    return columns, matrix, members


def score_book(
    rows: list[dict[str, Any]], *, contamination: float = 0.02, min_members: int = MIN_MEMBERS
) -> AnomalyScores:
    """Score every member against the rest of the book."""
    if len(rows) < min_members:
        return AnomalyScores(
            members=len(rows),
            available=False,
            reason=f"{len(rows)} members; the detector needs {min_members}",
        )

    columns, matrix, members = _matrix(rows)
    if not columns:
        return AnomalyScores(
            members=len(rows),
            available=False,
            reason="no feature is present for every member",
        )

    try:
        import numpy as np
        from sklearn.ensemble import IsolationForest
    except ImportError as exc:  # pragma: no cover - the ml extra is absent
        return AnomalyScores(
            members=len(rows), available=False, reason=f"scikit-learn is not installed ({exc})"
        )

    forest = IsolationForest(
        n_estimators=200,
        contamination=contamination,
        # Fixed, so the same book scores the same way twice. An advisory score
        # that moves between runs is one nobody can act on.
        random_state=20260906,
    )
    data = np.asarray(matrix, dtype=float)
    forest.fit(data)

    # score_samples is higher for normal points; flipped and scaled so a larger
    # number means more unusual, which is the direction every reader expects.
    raw = forest.score_samples(data)
    low, high = float(raw.min()), float(raw.max())
    span = high - low
    scaled = [(high - value) / span if span > 0 else 0.0 for value in raw]

    return AnomalyScores(
        scores=dict(zip(members, (round(value, 4) for value in scaled), strict=True)),
        columns=columns,
        members=len(rows),
    )
