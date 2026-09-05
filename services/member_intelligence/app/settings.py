"""member_intelligence service configuration."""

from __future__ import annotations

from functools import lru_cache

from cio_common.settings import Settings


class MemberIntelligenceSettings(Settings):
    service_name: str = "member_intelligence"
    service_port: int = 8003


@lru_cache(maxsize=1)
def settings() -> MemberIntelligenceSettings:
    return MemberIntelligenceSettings()
