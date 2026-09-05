"""core-stub configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class CoreStubSettings(Settings):
    service_name: str = "core_stub"
    service_port: int = 8010


@lru_cache(maxsize=1)
def settings() -> CoreStubSettings:
    return CoreStubSettings()
