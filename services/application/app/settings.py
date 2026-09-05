"""application service configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class ApplicationSettings(Settings):
    service_name: str = "application"
    service_port: int = 8001


@lru_cache(maxsize=1)
def settings() -> ApplicationSettings:
    return ApplicationSettings()
