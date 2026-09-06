"""gateway configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class GatewaySettings(Settings):
    service_name: str = "gateway"
    service_port: int = 8000
    upstream_timeout_seconds: float = 60.0
    #: A model call takes as long as it takes to decode. Measured on the GB10,
    #: one Council agent with a schema-constrained answer runs about thirty
    #: seconds, and five in parallel queue behind one another. Holding every
    #: upstream to a reader's deadline turned that into a 500 the caller could
    #: not tell from a real failure.
    llm_timeout_seconds: float = 300.0


@lru_cache(maxsize=1)
def settings() -> GatewaySettings:
    return GatewaySettings()
