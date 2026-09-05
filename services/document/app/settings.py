"""document service configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class DocumentSettings(Settings):
    service_name: str = "document"
    service_port: int = 8002


@lru_cache(maxsize=1)
def settings() -> DocumentSettings:
    return DocumentSettings()
