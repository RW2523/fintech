"""gateway configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class GatewaySettings(Settings):
    service_name: str = "gateway"
    service_port: int = 8000
    upstream_timeout_seconds: float = 60.0


@lru_cache(maxsize=1)
def settings() -> GatewaySettings:
    return GatewaySettings()
