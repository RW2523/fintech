"""notification service configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class NotificationSettings(Settings):
    service_name: str = "notification"
    service_port: int = 8014


@lru_cache(maxsize=1)
def settings() -> NotificationSettings:
    return NotificationSettings()
