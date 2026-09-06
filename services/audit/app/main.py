"""audit service entrypoint (docs/04 §6, docs/09 §6)."""

from __future__ import annotations

import asyncio
import contextlib
from functools import partial

from app.db import dispose, engine, session
from app.ingest import consume
from app.routes import router
from app.schema import apply_ddl
from app.settings import settings
from cio_common.outbox import run_dispatcher
from cio_common.service import create_app

_dispatcher: asyncio.Task[None] | None = None
_stop = asyncio.Event()


async def _startup(_app: object) -> None:
    global _dispatcher
    async with engine().begin() as connection:
        await apply_ddl(connection)

    # The trail is fed from the outbox, so a producer's write is never blocked
    # by this service being down and an event is never lost when it is.
    _stop.clear()
    _dispatcher = asyncio.create_task(
        run_dispatcher(session, {"audit": partial(consume, session)}, stop=_stop)
    )


async def _shutdown(_app: object) -> None:
    _stop.set()
    if _dispatcher is not None:
        _dispatcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _dispatcher
    await dispose()


app = create_app(
    "audit",
    version="0.1.0",
    routers=[router],
    settings=settings(),
    on_startup=_startup,
    on_shutdown=_shutdown,
)
