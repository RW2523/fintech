"""OpenTelemetry bootstrap (docs/13 §5).

Every service calls :func:`init_otel` once at startup. Traces, metrics and logs
go to the collector; spans carry `case_id`, `run_id` and `trace_id` so a
DecisionRecord can be opened as a trace in Grafana.
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any

from cio_common.settings import get_settings

__all__ = ["current_trace_id", "init_otel", "inject_context", "span_attributes"]

log = logging.getLogger(__name__)
_INITIALISED: set[str] = set()


def init_otel(service_name: str, app: Any = None) -> None:
    """Wire tracing for ``service_name``; instrument ``app`` when given.

    Never raises: observability must not stop a service from starting.
    """
    if service_name in _INITIALISED:
        return
    settings = get_settings()
    if not settings.otel_enabled:
        log.info("OTel disabled for %s", service_name)
        _INITIALISED.add(service_name)
        return

    # A service that was never told where to send traces does not send any.
    # The setting has a default so a developer can point at a local collector
    # without configuring one, but that default made every unit test open a
    # connection to a collector that is not there: the suite spent its time
    # retrying exports and printed a screen of connection errors per file.
    if "OTEL_EXPORTER_OTLP_ENDPOINT" not in os.environ:
        log.debug("no OTLP endpoint configured; %s runs untraced", service_name)
        _INITIALISED.add(service_name)
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create(
            {
                "service.name": f"cio-{service_name}",
                "service.namespace": "credit-intelligence-os",
                "deployment.environment": settings.cio_env,
            }
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces"))
        )
        trace.set_tracer_provider(provider)

        if app is not None:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            # Health checks and Prometheus scrapes are excluded. Every service
            # is polled for both several times a minute, and each poll was a
            # trace: the real ones were needles in a haystack the platform
            # generated itself, at four traces a minute per service.
            FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls="health,metrics")

        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument(tracer_provider=provider)
        _INITIALISED.add(service_name)
        log.info("OTel initialised for %s -> %s", service_name, settings.otel_exporter_otlp_endpoint)
    except Exception:
        log.warning("OTel setup failed for %s; continuing without tracing", service_name, exc_info=True)
        _INITIALISED.add(service_name)


def current_trace_id() -> str | None:
    """The active W3C trace id, or None outside a span."""
    # Never let telemetry break a request path.
    with contextlib.suppress(Exception):
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            return format(context.trace_id, "032x")
    return None


def inject_context(headers: dict[str, str]) -> dict[str, str]:
    """Add W3C trace context to outgoing headers, in place.

    The gateway forwards a `traceparent` it was given, so a caller that sends
    one gets a joined-up trace. A caller that sends none -- a browser, a curl,
    the workbench -- got a gateway span and nothing behind it, because nothing
    injected the context the gateway had just created. Every trace in Tempo was
    one service deep.
    """
    with contextlib.suppress(Exception):
        from opentelemetry.propagate import inject

        inject(headers)
    return headers


def span_attributes(**kwargs: Any) -> None:
    """Attach correlation attributes to the active span."""
    with contextlib.suppress(Exception):
        from opentelemetry import trace

        span = trace.get_current_span()
        for key, value in kwargs.items():
            if value is not None:
                span.set_attribute(key, value)
