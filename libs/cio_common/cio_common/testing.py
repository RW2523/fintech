"""Where a service's tests point their database.

Service tests truncate the tables they exercise, which is the only way to test
a hash-chained ledger from a known start. Pointed at the demo database that
destroys the seeded population, the loaded documents and the decisions in front
of the workbench, and the damage is silent: the suite passes and the demo is
empty.

So tests get their own database on the same server, created on first use. The
demo database is never touched unless someone names it explicitly through
`TEST_DATABASE_URL`, which is a deliberate act rather than a default.
"""

from __future__ import annotations

import asyncio
import os
import socket
from functools import cache, lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

__all__ = [
    "DEFAULT_TEST_DATABASE",
    "ensure_test_database",
    "postgres_is_up",
    "prepared_test_database_url",
    "seeded_database_url",
    "test_database_url",
]

#: Deliberately not the demo database's name.
DEFAULT_TEST_DATABASE = "cio_test"


def _env_file_values(root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    env = root / "docker" / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


@lru_cache(maxsize=1)
def test_database_url(root: Path | None = None) -> str:
    """The URL a service's tests should use.

    `TEST_DATABASE_URL` wins, for a caller who means it. Otherwise the server
    from `DATABASE_URL` or `docker/.env`, with the database name replaced.
    """
    if explicit := os.environ.get("TEST_DATABASE_URL"):
        return explicit

    base = os.environ.get("DATABASE_URL")
    if not base:
        values = _env_file_values(root or Path.cwd())
        base = (
            f"postgresql+asyncpg://{values.get('POSTGRES_USER', 'cio')}:"
            f"{values.get('POSTGRES_PASSWORD', '')}@localhost:"
            f"{values.get('POSTGRES_PORT', '5432')}/{values.get('POSTGRES_DB', 'cio')}"
        )
    return _with_database(base, os.environ.get("TEST_DATABASE_NAME", DEFAULT_TEST_DATABASE))


def postgres_is_up(host: str = "localhost", port: int = 5432, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


async def ensure_test_database(url: str | None = None) -> str:
    """Create the test database if it is not there yet, and return its URL.

    CREATE DATABASE cannot run inside a transaction, so this connects to the
    server's default database with autocommit. Racing runs are fine: the
    second one sees the first's database and moves on.
    """
    import asyncpg  # imported here so the library does not need it at runtime

    target = url or test_database_url()
    parts = urlsplit(target)
    name = parts.path.lstrip("/")

    # asyncpg speaks plain postgres URLs, not SQLAlchemy's dialect form.
    admin = urlunsplit(("postgresql", parts.netloc, "/postgres", "", ""))

    connection = await asyncpg.connect(admin)
    try:
        exists = await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
        if not exists:
            await connection.execute(f'CREATE DATABASE "{name}"')
    finally:
        await connection.close()
    return target


@cache
def _created(url: str) -> str:
    """Create the database once per process, whatever asks for it."""
    asyncio.run(ensure_test_database(url))
    return url


def prepared_test_database_url(root: Path | None = None) -> str:
    """The test database URL, with the database created if it was not there.

    Called from a synchronous fixture, so starting a loop here is safe. A
    second call in the same process is a cache hit and starts nothing.
    """
    return _created(test_database_url(root))


def seeded_database_url(root: Path | None = None) -> str:
    """The demo database, for the tests that read the generated population.

    The default above is the right one and this is the exception. A test that
    computes features over a member's twenty-event history cannot build that
    member in a fixture without rebuilding the generator, so it reads the one
    the generator made.

    Only for tests that never truncate and clean up exactly what they created.
    A test that empties a table here empties the demo, and the damage is
    silent: the suite passes and the workbench is blank.
    """
    base = os.environ.get("DATABASE_URL")
    if base:
        return base
    values = _env_file_values(root or Path.cwd())
    return (
        f"postgresql+asyncpg://{values.get('POSTGRES_USER', 'cio')}:"
        f"{values.get('POSTGRES_PASSWORD', '')}@localhost:"
        f"{values.get('POSTGRES_PORT', '5432')}/{values.get('POSTGRES_DB', 'cio')}"
    )
