"""llm_gateway service configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class LlmGatewaySettings(Settings):
    service_name: str = "llm_gateway"
    service_port: int = 8020


@lru_cache(maxsize=1)
def settings() -> LlmGatewaySettings:
    return LlmGatewaySettings()
