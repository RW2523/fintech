"""The shared FastAPI application factory every service uses (docs/01 §4).

One place to wire health, version, error envelopes, correlation headers and
OpenTelemetry, so eighteen services do not drift apart.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from cio_common.errors import CioError
from cio_common.metrics import CONTENT_TYPE_LATEST, render, time_request
from cio_common.otel import current_trace_id, init_otel
from cio_common.settings import Settings, get_settings

__all__ = ["create_app"]

TRACE_HEADER = "X-Trace-Id"


async def _maybe_await(value: Any) -> dict[str, Any]:
    if hasattr(value, "__await__"):
        value = await value
    return dict(value)


def create_app(
    service: str,
    *,
    version: str = "0.1.0",
    routers: Sequence[APIRouter] = (),
    settings: Settings | None = None,
    on_startup: Any = None,
    on_shutdown: Any = None,
    version_detail: Any = None,
) -> FastAPI:
    """Build the app for ``service`` with the platform conventions applied."""
    config = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        if on_startup is not None:
            await on_startup(app)
        yield
        if on_shutdown is not None:
            await on_shutdown(app)

    app = FastAPI(
        title=f"cio-{service}",
        version=version,
        description=f"Credit Intelligence OS — {service} service",
        lifespan=lifespan,
    )
    app.state.service = service
    app.state.version = version
    app.state.settings = config

    # Before the app serves anything, not in the lifespan. FastAPI builds its
    # middleware stack on the first request, and the instrumentation adds
    # middleware: adding it from a startup hook is silently too late, so twelve
    # of the eighteen services produced no HTTP spans at all. The six that did
    # were the ones making outbound calls, whose spans come from the httpx
    # client rather than from the app.
    init_otel(service, app)

    # Registered before `correlate` so it is the outer middleware and times
    # the whole handling, including the correlation header.
    app.middleware("http")(time_request(service))

    @app.middleware("http")
    async def correlate(request: Request, call_next: Any) -> Any:
        """Every response carries a trace id (docs/08 preamble).

        A handler that already set one (the gateway mints ids for inbound
        calls) keeps it; this only fills the gap.
        """
        response = await call_next(request)
        if not response.headers.get(TRACE_HEADER):
            response.headers[TRACE_HEADER] = request.headers.get(TRACE_HEADER) or current_trace_id() or "-"
        return response

    @app.exception_handler(CioError)
    async def documented_error(_request: Request, exc: CioError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.to_payload())

    @app.get("/health", tags=["meta"], summary="Liveness and version")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": service, "version": version}

    @app.get("/metrics", tags=["meta"], summary="Prometheus scrape", include_in_schema=False)
    async def metrics() -> Response:
        """docs/13 §5. Prometheus scrapes this on every service.

        It scraped it before this existed too, and got a 404 every fifteen
        seconds from all eighteen, which is why the dashboards drew nothing.
        """
        return Response(content=render(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/version", tags=["meta"], summary="Build and configuration version")
    async def version_info() -> dict[str, Any]:
        body: dict[str, Any] = {"service": service, "version": version, "environment": config.cio_env}
        if version_detail is None:
            return body
        # A service that serves a model reports the model's version here,
        # because the CaseSnapshot freeze stamps whatever this returns
        # (docs/03 §1). A service router cannot add it: this route is
        # registered first and would shadow theirs.
        try:
            body.update(await _maybe_await(version_detail()))
        except Exception as exc:
            # Fail safe: an unreportable version is said to be unavailable, so
            # a case frozen now is visibly distinguishable from one frozen
            # against a known model.
            body["version"] = "0.0.0-unavailable"
            body["available"] = False
            body["detail"] = str(exc)
        return body

    for router in routers:
        app.include_router(router)

    return app
