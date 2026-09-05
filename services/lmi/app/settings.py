"""lmi service configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class LmiSettings(Settings):
    service_name: str = "lmi"
    service_port: int = 8008


@lru_cache(maxsize=1)
def settings() -> LmiSettings:
    return LmiSettings()
