"""fraud service configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class FraudSettings(Settings):
    service_name: str = "fraud"
    service_port: int = 8007


@lru_cache(maxsize=1)
def settings() -> FraudSettings:
    return FraudSettings()
