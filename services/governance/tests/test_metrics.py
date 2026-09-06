"""T-072 — the governed metrics (docs/08 §6, docs/09 §7.1).

Two properties are tested here and neither is about a particular number.

The first is that no metric can return a member, a case or an account. That is
what makes the set safe to grant to a copilot, and it has to hold for every
metric rather than for the ones somebody remembered to check.

The second is that a rate over nothing is not zero. A tile that draws "no
accounts were late" and "no accounts existed" the same way reports a healthy
month when it should report an empty one.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from app.metrics import METRICS, _by_month, _rate, compute, metric_names

#: Anything that would name one member, one case or one account.
INDIVIDUAL = re.compile(
    r"\b(M-\d{4,}|A-\d{4,}|case_[0-9A-HJKMNP-TV-Z]{4,}|"
    r"(snap|dr|ev|hnd|cnv)_[0-9A-HJKMNP-TV-Z]{4,})\b"
)


def test_every_metric_publishes_what_it_measures() -> None:
    """A number without its definition invites the wrong reading.

    "Autonomous share 0.25" is unusable without knowing whether it is of all
    decisions or of the ones eligible to be automatic, and the two answers
    would lead a manager to opposite conclusions about the dial.
    """
    for name in metric_names():
        metric = METRICS[name]
        assert metric.means.strip(), f"{name} does not say what it measures"
        assert metric.means.rstrip().endswith("."), f"{name}'s definition is not a sentence"


def test_a_rate_over_nothing_is_not_zero() -> None:
    assert _rate(0, 0) is None
    assert _rate(0, 4) == 0.0
    assert _rate(1, 4) == 0.25


def test_a_series_is_grouped_by_month_and_carries_its_rate() -> None:
    rows = [
        {"created_at": "2026-07-14T09:00:00Z", "decided": True},
        {"created_at": "2026-07-20T09:00:00Z", "decided": False},
        {"created_at": "2026-08-01T09:00:00Z", "decided": True},
    ]
    series = _by_month(rows, count=lambda r: bool(r.get("decided")))
    assert [row["month"] for row in series] == ["2026-07", "2026-08"]
    assert series[0] == {"month": "2026-07", "total": 2, "matched": 1, "rate": 0.5}


def test_a_row_without_a_date_is_left_out_rather_than_bucketed() -> None:
    """Dropped, not put in a month it might not belong to.

    A row with no timestamp assigned to the current month would make this
    month's rate wrong in a way nobody could see.
    """
    series = _by_month([{"decided": True}], count=lambda r: True)
    assert series == []


class Fake:
    """A source that answers every read with a fixed body."""

    def __init__(self, bodies: dict[str, Any]) -> None:
        self.bodies = bodies
        self.asked: list[str] = []

    async def __call__(self, service: str, path: str, **params: Any) -> Any:
        self.asked.append(f"{service}{path}")
        return self.bodies.get(service, {})


@pytest.fixture
def sources(monkeypatch: pytest.MonkeyPatch) -> Fake:
    fake = Fake(
        {
            "decision": {
                "decisions": [
                    {
                        "decision_record_id": "dr_01ARZ3NDEKTSV4RRFFQ69G5FAW",
                        "case_id": "case_01ARZ3NDEKTSV4RRFFQ69G5FAW",
                        "member_id": "M-000042",
                        "route": "AUTONOMOUS",
                        "recommendation": "APPROVE",
                        "tier": "STANDARD",
                        "required_authority": "CREDIT_OFFICER",
                        "created_at": "2026-09-01T09:00:00Z",
                        "decided": True,
                    }
                ]
            },
            "lmi": {"members": [{"member_id": "M-000042", "state": "WATCH"}]},
            "core_stub": {"series": [{"month": "2026-08-01", "accounts": 10, "late30": 1}]},
            "notification": {
                "handoffs": [
                    {"handoff_id": "hnd_01ARZ3NDEKTSV4RRFFQ69G5FAW", "signal": "HARDSHIP", "state": "OPEN"}
                ]
            },
        }
    )
    monkeypatch.setattr("app.metrics._get", fake)
    return fake


@pytest.mark.parametrize("name", sorted(METRICS))
async def test_no_metric_can_return_an_individual(name: str, sources: Fake) -> None:
    """The property that makes this set safe to hand to a copilot.

    Every source in the fixture carries a member id, a case id and a handoff
    id. If any of them reaches the answer, a copilot granted `metrics.query`
    has a reach nobody asked it to have, and it will eventually use it.
    """
    import json

    answer = await compute(name, days=90)
    found = INDIVIDUAL.findall(json.dumps(answer))
    assert not found, f"{name} leaked {found[:3]}"


async def test_a_metric_names_what_it_read(sources: Fake) -> None:
    answer = await compute("early_warning", days=90)
    assert answer["sources"] == ["lmi"]


async def test_an_unknown_metric_is_a_404_that_lists_the_real_ones(sources: Fake) -> None:
    from cio_common.errors import NotFound

    with pytest.raises(NotFound) as caught:
        await compute("approvalz", days=90)
    assert "applications" in str(caught.value.details["metrics"])
