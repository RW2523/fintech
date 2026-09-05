"""Async database access: engine, sessions and per-service schemas (docs/04)."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from cio_common.settings import get_settings

__all__ = ["dispose_engine", "get_engine", "schema_for", "session", "session_factory"]

# One schema per service plus the shared ones (docs/04 preamble).
_SHARED_SCHEMAS = {"events", "ledger", "audit", "core"}


def schema_for(service: str) -> str:
    """`policy` -> `app_policy`; shared schemas keep their own name."""
    if service in _SHARED_SCHEMAS:
        return service
    return f"app_{service}"


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        echo=False,
    )


@lru_cache(maxsize=1)
def session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


@contextlib.asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    """A transactional session. Commits on success, rolls back on error."""
    async with session_factory()() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


async def dispose_engine() -> None:
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
        get_engine.cache_clear()
        session_factory.cache_clear()
