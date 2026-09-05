"""governance service configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class GovernanceSettings(Settings):
    service_name: str = "governance"
    service_port: int = 8016


@lru_cache(maxsize=1)
def settings() -> GovernanceSettings:
    return GovernanceSettings()
