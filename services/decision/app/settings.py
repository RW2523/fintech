"""decision service configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class DecisionSettings(Settings):
    service_name: str = "decision"
    service_port: int = 8012


@lru_cache(maxsize=1)
def settings() -> DecisionSettings:
    return DecisionSettings()
