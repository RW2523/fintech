"""Service configuration from the environment (CLAUDE.md §7)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    """Every knob a service reads. Documented in docker/.env.example."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # --- identity of this process ---
    service_name: str = "unknown"
    cio_env: Literal["demo", "dev", "test", "pilot", "prod"] = "demo"
    log_level: str = "info"

    # --- datastores ---
    database_url: str = "postgresql+asyncpg://cio:change-me@localhost:5432/cio"
    redis_url: str = "redis://localhost:6379/0"
    minio_endpoint: str = "localhost:9000"
    minio_root_user: str = "cio"
    minio_root_password: str = "change-me-too"  # noqa: S105 - dev default, see below

    # --- workflow ---
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "cio"

    # --- secrets ---
    # Long enough for HMAC-SHA256. docker/.env generates real random values;
    # these defaults exist only so tests and local runs work out of the box.
    jwt_secret: str = "dev-only-insecure-jwt-secret-do-not-ship"  # noqa: S105
    token_secret: str = "dev-only-insecure-token-secret-do-not-ship"  # noqa: S105
    internal_key: str = "dev-only-insecure-internal-key-do-not-ship"
    jwt_ttl_seconds: int = 8 * 3600

    # --- observability ---
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    otel_enabled: bool = True

    # --- committee budgets (docs/02 §5) ---
    tier1_budget_seconds: float = 60.0
    tier2_budget_seconds: float = 180.0
    tier1_token_budget: int = 60_000
    tier2_token_budget: int = 150_000

    # --- safety ---
    debug_prompts: bool = Field(
        default=False,
        validation_alias="CIO_DEBUG_PROMPTS",
        description="Log raw prompts. Refused outside dev (docs/13 §2).",
    )

    @field_validator("jwt_secret", "token_secret", "internal_key")
    @classmethod
    def _secrets_are_long_enough(cls, value: str, info) -> str:  # type: ignore[no-untyped-def]
        if len(value) < 32:
            raise ValueError(
                f"{info.field_name} must be at least 32 characters (RFC 7518 §3.2 for HMAC-SHA256)"
            )
        return value

    @field_validator("jwt_secret", "token_secret", "internal_key")
    @classmethod
    def _real_secrets_outside_demo(cls, value: str, info) -> str:  # type: ignore[no-untyped-def]
        if "dev-only-insecure" in value and info.data.get("cio_env") in ("pilot", "prod"):
            raise ValueError(f"{info.field_name} still holds its development default")
        return value

    @field_validator("debug_prompts")
    @classmethod
    def _no_prompt_logging_outside_dev(cls, value: bool, info) -> bool:  # type: ignore[no-untyped-def]
        if value and info.data.get("cio_env") in ("demo", "pilot", "prod"):
            raise ValueError("CIO_DEBUG_PROMPTS is refused outside dev (docs/13 §2)")
        return value

    @property
    def is_demo(self) -> bool:
        return self.cio_env == "demo"

    def sync_database_url(self) -> str:
        """The same database over psycopg/asyncpg-free drivers, for Alembic."""
        return self.database_url.replace("+asyncpg", "")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
