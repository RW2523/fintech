"""audit service configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class AuditSettings(Settings):
    service_name: str = "audit"
    service_port: int = 8015


@lru_cache(maxsize=1)
def settings() -> AuditSettings:
    return AuditSettings()
