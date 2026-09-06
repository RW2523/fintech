"""agent-runtime service entrypoint (docs/06 §1)."""

from __future__ import annotations

from app.db import dispose
from app.routes import router
from app.settings import settings
from cio_common.service import create_app


async def _shutdown(_app: object) -> None:
    await dispose()


app = create_app(
    "agent_runtime", version="0.1.0", routers=[router], settings=settings(), on_shutdown=_shutdown
)
