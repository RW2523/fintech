"""feature service configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class FeatureSettings(Settings):
    service_name: str = "feature"
    service_port: int = 8005


@lru_cache(maxsize=1)
def settings() -> FeatureSettings:
    return FeatureSettings()
