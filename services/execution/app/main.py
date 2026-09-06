"""execution service entrypoint (docs/08 §7).

The only service that changes anything outside the platform.
"""

from __future__ import annotations

from app.db import dispose, engine
from app.routes import router
from app.schema import apply_ddl
from app.settings import settings
from cio_common.service import create_app


async def _startup(_app: object) -> None:
    async with engine().begin() as connection:
        await apply_ddl(connection)


async def _shutdown(_app: object) -> None:
    await dispose()


app = create_app(
    "execution",
    version="0.1.0",
    routers=[router],
    settings=settings(),
    on_startup=_startup,
    on_shutdown=_shutdown,
)
