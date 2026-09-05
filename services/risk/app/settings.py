"""risk service configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class RiskSettings(Settings):
    service_name: str = "risk"
    service_port: int = 8006


@lru_cache(maxsize=1)
def settings() -> RiskSettings:
    return RiskSettings()
