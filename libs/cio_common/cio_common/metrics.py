"""What every service reports about itself (docs/13 §5).

Prometheus scrapes `/metrics` on all eighteen services
(`infra/observability/config/prometheus.yml`). Until this module existed, every
one of those scrapes was a 404 and every Grafana panel drew an empty graph, so
the platform was fully traced and entirely unmeasured.

Two kinds of metric live here.

The **shared** ones are recorded by `create_app` for every service without the
service knowing: request duration, request count, in-flight requests. They are
what answers "is anything slow" and "is anything erroring", which is the
question an operator asks first and the one no service should have to
instrument for itself.

The **domain** ones are recorded by the service that owns the number, because
only it knows what the number means: tokens spent on an LLM route, the age of
the decision queue, how long the nightly longitudinal run took. Each is
declared here so the names cannot drift, and every one carries the unit in its
name as Prometheus expects.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

__all__ = [
    "CONTENT_TYPE_LATEST",
    "committee_run_seconds",
    "decision_queue_depth",
    "ledger_entries",
    "llm_errors",
    "llm_request_seconds",
    "llm_tokens",
    "lmi_members_in_state",
    "lmi_run_seconds",
    "render",
    "request_seconds",
    "requests_in_flight",
    "time_request",
    "tool_calls",
]

#: Buckets chosen for this platform rather than taken from a default. A
#: request under 100ms is a read; one over 30s is a model. The default buckets
#: stop at 10s and would put every Council invocation in the same bucket.
_HTTP_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)

#: Model calls are slow and the interesting range is minutes, not milliseconds.
_LLM_BUCKETS = (0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 45.0, 60.0, 90.0, 120.0, 180.0, 300.0)

request_seconds = Histogram(
    "cio_http_request_seconds",
    "How long a request took, by service and route.",
    ("service", "method", "route", "status"),
    buckets=_HTTP_BUCKETS,
)

requests_in_flight = Gauge(
    "cio_http_requests_in_flight",
    "Requests being served right now, by service.",
    ("service",),
)

llm_request_seconds = Histogram(
    "cio_llm_request_seconds",
    "How long one model call took, by route and provider.",
    ("route", "provider", "outcome"),
    buckets=_LLM_BUCKETS,
)

llm_tokens = Counter(
    "cio_llm_tokens_total",
    "Tokens spent, by route and direction. The bill, and the budget.",
    ("route", "provider", "direction"),
)

llm_errors = Counter(
    "cio_llm_errors_total",
    "Model calls that did not return a usable answer, by why.",
    ("route", "provider", "kind"),
)

tool_calls = Counter(
    "cio_tool_calls_total",
    "Tool invocations, by tool and whether the registry allowed them.",
    ("tool", "agent_id", "outcome"),
)

committee_run_seconds = Histogram(
    "cio_committee_run_seconds",
    "End-to-end time of one committee run, by tier.",
    ("tier", "outcome"),
    buckets=_LLM_BUCKETS,
)

lmi_run_seconds = Histogram(
    "cio_lmi_run_seconds",
    "How long a longitudinal batch took, by what it was doing.",
    ("stage",),
    buckets=(1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0),
)

decision_queue_depth = Gauge(
    "cio_decision_queue_depth",
    "Decisions waiting for a person, by route.",
    ("route",),
)

lmi_members_in_state = Gauge(
    "cio_lmi_members_in_state",
    "Members in each early-warning state, after the last evaluation.",
    ("state",),
)

ledger_entries = Gauge(
    "cio_ledger_entries",
    "Entries in each append-only chain. Two chains, deliberately separate.",
    ("chain",),
)


def render() -> bytes:
    """The scrape body."""
    return generate_latest()


def time_request(
    service: str,
) -> Callable[[Any, Callable[[Any], Awaitable[Any]]], Awaitable[Any]]:
    """Middleware that records duration and count for every request.

    The route template is used rather than the path, so a thousand case ids do
    not become a thousand time series. A metric with unbounded cardinality
    takes Prometheus down, which is a worse outage than the one it was added to
    detect.
    """

    async def middleware(request: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
        started = time.perf_counter()
        requests_in_flight.labels(service=service).inc()
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            requests_in_flight.labels(service=service).dec()
            route = request.scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            request_seconds.labels(
                service=service,
                method=request.method,
                route=template,
                status=status,
            ).observe(time.perf_counter() - started)

    return middleware
