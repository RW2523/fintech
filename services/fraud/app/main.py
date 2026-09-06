"""fraud service entrypoint (docs/07 §3)."""

from __future__ import annotations

from app.db import dispose
from app.routes import router, version_detail
from app.settings import settings
from cio_common.service import create_app


async def _shutdown(_app: object) -> None:
    await dispose()


app = create_app(
    "fraud",
    version="0.1.0",
    routers=[router],
    settings=settings(),
    on_shutdown=_shutdown,
    version_detail=version_detail,
)
