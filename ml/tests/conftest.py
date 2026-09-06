"""Fixtures for the model tests."""

from __future__ import annotations

import socket
from functools import lru_cache

import pytest

from ml.common import registry


@lru_cache(maxsize=1)
def _postgres_is_up() -> bool:
    try:
        with socket.create_connection(("localhost", 5432), 2):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def artifacts():
    """The trained credit-risk version serving is pointed at."""
    try:
        return registry.load("credit_risk")
    except registry.ArtifactError as exc:
        pytest.skip(f"no trained credit-risk artifacts: {exc}")


@pytest.fixture
def needs_database() -> None:
    if not _postgres_is_up():
        pytest.skip("PostgreSQL not reachable; run `make up`")
