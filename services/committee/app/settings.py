"""committee service configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class CommitteeSettings(Settings):
    service_name: str = "committee"
    service_port: int = 8009


@lru_cache(maxsize=1)
def settings() -> CommitteeSettings:
    return CommitteeSettings()
