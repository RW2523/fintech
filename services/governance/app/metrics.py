"""The governed metrics (T-072, docs/08 §6, docs/09 §7.1).

One definition per metric, in one place, computed once. The cockpit tile and
the manager copilot both read the same endpoint, so a tile cannot drift from
the number the copilot quotes: they are the same number, not two calculations
that agree today.

Every metric is an aggregate. Nothing here returns a member, a case or an
account id, because the manager copilot is granted this and a copilot that can
name a member has a reach nobody asked it to have. The one exception is a
branch or a product code, which are the dimensions a manager asks about.

Each answer carries `sources`: which services were read to produce it. A
number whose provenance is unrecorded is a number nobody can check, and the
first question a manager asks about a surprising figure is where it came from.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from cio_common.errors import CioError, NotFound

__all__ = ["METRICS", "Metric", "compute", "metric_names"]


def _url(name: str, default: str) -> str:
    return os.environ.get(name, default).rstrip("/")


async def _get(service: str, path: str, **params: Any) -> Any:
    """Read one service, and say which one failed when it does."""
    # Ports from services/gateway/app/routing.py, which is the one place they
    # are written down. Guessing them cost three metrics an "unreachable".
    base = {
        "decision": _url("DECISION_URL", "http://decision:8012"),
        "core_stub": _url("CORE_STUB_URL", "http://core_stub:8010"),
        "lmi": _url("LMI_URL", "http://lmi:8008"),
        "notification": _url("NOTIFICATION_URL", "http://notification:8014"),
        "application": _url("APPLICATION_URL", "http://application:8001"),
    }[service]
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.get(f"{base}{path}", params={k: v for k, v in params.items() if v})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Separated from a connection failure on purpose. A service that
            # answered 422 is working and was asked the wrong question, and
            # calling that "unreachable" sends an operator to the network.
            raise CioError(
                f"the {service} service refused the request",
                detail=f"{exc.response.status_code}: {exc.response.text[:300]}",
            ) from exc
        except httpx.HTTPError as exc:
            raise CioError(f"the {service} service is unreachable", detail=str(exc)) from exc
    return response.json()


@dataclass(frozen=True, slots=True)
class Metric:
    """One published number, and what a reader has to know to use it."""

    name: str
    #: What it measures, in a sentence. Returned with every answer: a manager
    #: reading "autonomous share 0.12" needs to know whether that is of all
    #: decisions or of the ones eligible to be automatic.
    means: str
    dimensions: tuple[str, ...]
    build: Callable[[int, str | None], Awaitable[dict[str, Any]]]


def _rate(part: int, whole: int) -> float | None:
    """A share, or nothing when there was nothing to take a share of.

    Zero and "no cases" are different facts. A tile that draws both as 0%
    reports a healthy month when it should report an empty one.
    """
    return round(part / whole, 4) if whole else None


# ---------------------------------------------------------------------------
# flow
# ---------------------------------------------------------------------------
async def _decisions(days: int) -> list[dict[str, Any]]:
    body = await _get("decision", "/queue", limit=5000)
    since = datetime.now(UTC) - timedelta(days=max(days, 1))
    rows = []
    for row in body.get("decisions") or []:
        created = str(row.get("created_at") or "")
        if created and created[:10] < since.date().isoformat():
            continue
        rows.append(row)
    return rows


def _by_month(rows: list[dict[str, Any]], *, count: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
    """A month-by-month series, so a question about a trend has one to read.

    Without this every flow metric is a single figure for the whole window, and
    "why did approvals fall" cannot be answered from it. Asked anyway, the
    copilot found two numbers in the context that differed and reported a fall
    between them: it said the approval rate fell from 0.5 to 0.25 when 0.25 was
    the autonomous share. A model given no series will build one.
    """
    buckets: dict[str, dict[str, int]] = {}
    for row in rows:
        month = str(row.get("created_at") or "")[:7]
        if not month:
            continue
        bucket = buckets.setdefault(month, {"total": 0, "matched": 0})
        bucket["total"] += 1
        if count(row):
            bucket["matched"] += 1
    return [
        {"month": month, **counts, "rate": _rate(counts["matched"], counts["total"])}
        for month, counts in sorted(buckets.items())
    ]


async def _applications(days: int, product: str | None) -> dict[str, Any]:
    rows = await _decisions(days)
    if product:
        rows = [r for r in rows if str(r.get("product_code") or product) == product]
    decided = [r for r in rows if r.get("decided")]
    return {
        "rows": [
            {
                "received": len(rows),
                "decided": len(decided),
                "pending": len(rows) - len(decided),
                "decided_share": _rate(len(decided), len(rows)),
            }
        ],
        "series": _by_month(rows, count=lambda r: bool(r.get("decided"))),
        "series_means": "`rate` is the share of that month's cases that have been decided.",
        "sources": ["decision"],
    }


async def _routing(days: int, product: str | None) -> dict[str, Any]:
    """Where the platform sent its work, and how much of it it kept.

    The autonomous share is of every decision, not of the ones eligible to be
    automatic. The second number flatters the dial and is the one a vendor
    would quote.
    """
    rows = await _decisions(days)
    if product:
        rows = [r for r in rows if str(r.get("product_code") or product) == product]
    by_route: dict[str, int] = {}
    for row in rows:
        by_route[str(row.get("route") or "UNKNOWN")] = by_route.get(str(row.get("route") or "UNKNOWN"), 0) + 1
    auto = by_route.get("AUTONOMOUS", 0)
    return {
        "rows": [
            {"route": route, "decisions": count, "share": _rate(count, len(rows))}
            for route, count in sorted(by_route.items())
        ],
        "totals": {"decisions": len(rows), "autonomous": auto, "autonomous_share": _rate(auto, len(rows))},
        "series": _by_month(rows, count=lambda r: str(r.get("route")) == "AUTONOMOUS"),
        "series_means": "`rate` is that month's autonomous share.",
        "sources": ["decision"],
    }


async def _recommendations(days: int, product: str | None) -> dict[str, Any]:
    rows = await _decisions(days)
    if product:
        rows = [r for r in rows if str(r.get("product_code") or product) == product]
    by_rec: dict[str, int] = {}
    for row in rows:
        key = str(row.get("recommendation") or "UNKNOWN")
        by_rec[key] = by_rec.get(key, 0) + 1
    approve = by_rec.get("APPROVE", 0)
    return {
        "rows": [
            {"recommendation": name, "count": count, "share": _rate(count, len(rows))}
            for name, count in sorted(by_rec.items())
        ],
        "totals": {"decisions": len(rows), "approval_rate": _rate(approve, len(rows))},
        "series": _by_month(rows, count=lambda r: str(r.get("recommendation")) == "APPROVE"),
        "series_means": "`rate` is that month's share recommended APPROVE.",
        "sources": ["decision"],
    }


async def _tiers(days: int, product: str | None) -> dict[str, Any]:
    rows = await _decisions(days)
    by_tier: dict[str, int] = {}
    for row in rows:
        by_tier[str(row.get("tier") or "UNKNOWN")] = by_tier.get(str(row.get("tier") or "UNKNOWN"), 0) + 1
    return {
        "rows": [
            {"tier": tier, "decisions": count, "share": _rate(count, len(rows))}
            for tier, count in sorted(by_tier.items())
        ],
        "sources": ["decision"],
    }


async def _authority(days: int, product: str | None) -> dict[str, Any]:
    """Which rung of the ladder the work landed on.

    A book whose decisions all need a head of credit is a book whose autonomy
    settings are wrong, and this is the tile that shows it.
    """
    rows = await _decisions(days)
    by_authority: dict[str, int] = {}
    for row in rows:
        key = str(row.get("required_authority") or "NONE")
        by_authority[key] = by_authority.get(key, 0) + 1
    return {
        "rows": [
            {"required_authority": name, "decisions": count, "share": _rate(count, len(rows))}
            for name, count in sorted(by_authority.items())
        ],
        "sources": ["decision"],
    }


# ---------------------------------------------------------------------------
# the book
# ---------------------------------------------------------------------------
async def _delinquency(days: int, product: str | None) -> dict[str, Any]:
    months = max(1, min(60, round(days / 30) or 1))
    body = await _get("core_stub", "/core/portfolio/monthly", months=months)
    series = body.get("series") or []
    # Under `series` as well as `rows`, because that is the key the output
    # screen counts to decide whether a movement over time can be claimed at
    # all, and this metric is the one that genuinely has a history.
    return {
        "rows": series,
        "series": series,
        "series_means": "One row per month of the outcome panel.",
        "sources": ["core_stub"],
    }


async def _early_warning(days: int, product: str | None) -> dict[str, Any]:
    """How many members are in each early-warning state.

    Counts only. The states themselves name members, and this endpoint is
    granted to a copilot that must never see one.
    """
    # 5,000 is the service's own ceiling. Asking for more is a 422, and a 422
    # arriving through an httpx raise_for_status reads as "unreachable", which
    # sent this looking for a network fault for ten minutes.
    body = await _get("lmi", "/lmi/states", limit=5000)
    by_state: dict[str, int] = {}
    for row in body.get("members") or []:
        by_state[str(row.get("state") or "UNKNOWN")] = by_state.get(str(row.get("state") or "UNKNOWN"), 0) + 1
    total = sum(by_state.values())
    return {
        "rows": [
            {"state": state, "members": count, "share": _rate(count, total)}
            for state, count in sorted(by_state.items())
        ],
        "totals": {"members": total},
        "sources": ["lmi"],
    }


async def _outreach(days: int, product: str | None) -> dict[str, Any]:
    """What was sent, and what came back.

    Contacts without outcomes is a metric that only ever goes up, which is why
    both are here: a campaign that sends four thousand reminders and records no
    promises has not been measured, it has been counted.
    """
    body = await _get("notification", "/handoffs", state="ALL")
    handoffs = body.get("handoffs") or []
    by_signal: dict[str, int] = {}
    for row in handoffs:
        by_signal[str(row.get("signal") or "ROUTINE")] = (
            by_signal.get(str(row.get("signal") or "ROUTINE"), 0) + 1
        )
    open_now = sum(1 for row in handoffs if row.get("state") == "OPEN")
    return {
        "rows": [{"signal": name, "handoffs": count} for name, count in sorted(by_signal.items())],
        "totals": {"handoffs": len(handoffs), "open": open_now},
        "sources": ["notification"],
    }


METRICS: dict[str, Metric] = {
    "applications": Metric(
        "applications",
        "Cases the platform has assessed in the window, and how many still wait for a person.",
        ("product",),
        _applications,
    ),
    "routing": Metric(
        "routing",
        "Where each decision was routed. The autonomous share is of every decision in the "
        "window, not of the ones eligible to be automatic.",
        ("product",),
        _routing,
    ),
    "recommendations": Metric(
        "recommendations",
        "What the platform recommended. The approval rate is the share recommended APPROVE, "
        "which is not the same as the share a person approved.",
        ("product",),
        _recommendations,
    ),
    "tiers": Metric(
        "tiers",
        "How much committee each case needed.",
        ("product",),
        _tiers,
    ),
    "authority": Metric(
        "authority",
        "The approval authority each decision required.",
        ("product",),
        _authority,
    ),
    "delinquency": Metric(
        "delinquency",
        "The book month by month: accounts late at each bucket, the share thirty days late, "
        "the roll rate from thirty to sixty days, and the cure rate out of thirty.",
        (),
        _delinquency,
    ),
    "early_warning": Metric(
        "early_warning",
        "Members in each early-warning state, as counts.",
        (),
        _early_warning,
    ),
    "outreach": Metric(
        "outreach",
        "Handoffs raised to a person, by signal, and how many are still open.",
        (),
        _outreach,
    ),
}


def metric_names() -> tuple[str, ...]:
    return tuple(sorted(METRICS))


async def compute(name: str, *, days: int = 90, product: str | None = None) -> dict[str, Any]:
    """One metric, with what a reader needs in order to use it."""
    metric = METRICS.get(name)
    if metric is None:
        raise NotFound(f"no metric {name!r}", metrics=list(metric_names()))

    built = await metric.build(days, product)
    return {
        "metric": name,
        "means": metric.means,
        "window_days": days,
        "product": product,
        "dimensions": list(metric.dimensions),
        "as_of": datetime.now(UTC).isoformat(),
        **built,
    }
