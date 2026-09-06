"""T-061 — the advisory anomaly detector (docs/07 §4.3).

Advisory means advisory. It never raises a state change on its own, because
nobody can say what it objected to. It earns its place by catching the
combination nobody wrote a rule for.
"""

from __future__ import annotations

import pytest

from app.anomaly import MIN_MEMBERS, score_book


def book(n: int, **overrides: dict[str, float]) -> list[dict[str, object]]:
    """A book of ordinary members, plus whichever odd ones are named."""
    rows: list[dict[str, object]] = [
        {
            "member_id": f"M-{index:06d}",
            "features": {
                "days_to_pay_median_30d": 1.0 + (index % 3),
                "savings_slope_180d": 2.0 + (index % 2),
                "deduction_missed_count_90d": 0.0,
            },
        }
        for index in range(n)
    ]
    for member_id, features in overrides.items():
        rows.append({"member_id": member_id, "features": features})
    return rows


def test_a_small_book_is_not_scored() -> None:
    """A member cannot be judged unusual against a book that has not been
    seen."""
    scored = score_book(book(10))
    assert scored.available is False
    assert "needs" in (scored.reason or "")


def test_a_member_unlike_the_book_scores_highest() -> None:
    pytest.importorskip("sklearn")
    odd = {
        "days_to_pay_median_30d": 45.0,
        "savings_slope_180d": -900.0,
        "deduction_missed_count_90d": 9.0,
    }
    scored = score_book(book(MIN_MEMBERS + 10, **{"M-ODD": odd}))

    assert scored.available is True
    ranked = sorted(scored.scores.items(), key=lambda item: -item[1])
    assert ranked[0][0] == "M-ODD"


def test_the_same_book_scores_the_same_way_twice() -> None:
    """An advisory score that moves between runs is one nobody can act on."""
    pytest.importorskip("sklearn")
    rows = book(MIN_MEMBERS + 5)
    assert score_book(rows).scores == score_book(rows).scores


def test_only_features_every_member_has_are_used() -> None:
    """A feature some members lack would be imputed, and an imputed value is a
    made-up observation the forest cannot tell from a real one."""
    pytest.importorskip("sklearn")
    rows = book(MIN_MEMBERS + 2)
    rows[0]["features"] = {**rows[0]["features"], "only_this_member_has_it": 5.0}  # type: ignore[dict-item]

    scored = score_book(rows)
    assert "only_this_member_has_it" not in scored.columns


def test_a_book_with_no_shared_feature_is_not_scored() -> None:
    rows = [{"member_id": f"M-{i:06d}", "features": {f"unique_{i}": 1.0}} for i in range(MIN_MEMBERS + 1)]
    scored = score_book(rows)
    assert scored.available is False
    assert "every member" in (scored.reason or "")
