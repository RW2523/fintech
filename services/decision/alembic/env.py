"""Alembic environment for decision.

The database URL comes from the service settings, never from alembic.ini, so a
migration cannot be pointed at the wrong database by editing a config file.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.settings import settings

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = None


def _url() -> str:
    return settings().sync_database_url().replace("postgresql://", "postgresql+psycopg://")


def _version_table() -> str:
    """Each service tracks its own migrations.

    They share one database, so a single `alembic_version` table would make
    every service see (and fail on) the others' revision ids.
    """
    return f"alembic_version_{settings().service_name}"


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        version_table=_version_table(),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool, future=True)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=_version_table(),
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
