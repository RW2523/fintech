"""policy service entrypoint.

Endpoints arrive in the task that implements this service; the scaffolding
gives it health, version, correlation headers, error envelopes and tracing.
"""

from __future__ import annotations

from app.db import dispose
from app.routes import dial_router, router
from app.settings import settings
from cio_common.service import create_app


async def _shutdown(_app: object) -> None:
    await dispose()


app = create_app(
    "policy", version="0.1.0", routers=[router, dial_router], settings=settings(), on_shutdown=_shutdown
)
