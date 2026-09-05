"""policy service configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class PolicySettings(Settings):
    service_name: str = "policy"
    service_port: int = 8004


@lru_cache(maxsize=1)
def settings() -> PolicySettings:
    return PolicySettings()
