"""execution service configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class ExecutionSettings(Settings):
    service_name: str = "execution"
    service_port: int = 8013


@lru_cache(maxsize=1)
def settings() -> ExecutionSettings:
    return ExecutionSettings()
