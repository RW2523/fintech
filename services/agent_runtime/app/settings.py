"""agent_runtime service configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class AgentRuntimeSettings(Settings):
    service_name: str = "agent_runtime"
    service_port: int = 8011


@lru_cache(maxsize=1)
def settings() -> AgentRuntimeSettings:
    return AgentRuntimeSettings()
