"""Database access for agent_runtime."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.settings import settings

#: This service owns `app_agent` (docs/04).
SCHEMAS = ["app_agent"]


@lru_cache(maxsize=1)
def engine() -> AsyncEngine:
    return create_async_engine(settings().database_url, pool_pre_ping=True, pool_size=10)


@lru_cache(maxsize=1)
def sessions() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine(), expire_on_commit=False, class_=AsyncSession)


@contextlib.asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    async with sessions()() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


async def dispose() -> None:
    if engine.cache_info().currsize:
        await engine().dispose()
        engine.cache_clear()
        sessions.cache_clear()
