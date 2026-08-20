"""Configuration, loaded from the environment and validated at startup.

If a required variable is missing the API refuses to boot and says exactly which
one and how to generate it. A platform that starts with a blank JWT secret and
fails later, in production, at 2am, is worse than one that will not start.
"""

from __future__ import annotations

import sys
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Minimum length for anything used as a signing key or a salt. 32 hex chars is
# 128 bits, which is the floor, not the target.
MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- database ---
    database_url: str = Field(..., description="Primary connection string")
    database_url_readonly: str | None = Field(
        default=None,
        description="Read-only role restricted to mart. Required only when AI is enabled.",
    )
    db_pool_min: int = 1
    db_pool_max: int = 10

    # --- security ---
    jwt_secret: str = Field(..., description="openssl rand -hex 32")
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 14
    pii_hash_salt: str = Field(..., description="openssl rand -hex 32; permanent once set")
    worker_trigger_secret: str = Field(..., description="Shared secret for /worker/trigger")

    # --- api ---
    api_host: str = "0.0.0.0"      # noqa: S104 - containers bind all interfaces
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000"
    max_upload_mb: int = 25
    ingest_max_concurrency: int = 4
    rate_limit_auth_per_minute: int = 10
    rate_limit_ingest_per_minute: int = 30
    upload_dir: str = "uploads"
    log_level: str = "INFO"
    environment: str = "development"

    # --- ai ---
    ai_enabled: bool = False
    anthropic_api_key: str | None = None
    ai_model: str = "claude-sonnet-5"
    ai_daily_token_budget: int = 200_000
    ai_request_timeout_seconds: int = 45

    # --- worker ---
    worker_enabled: bool = True
    fx_provider_url: str = "https://open.er-api.com/v6/latest"
    fx_provider_api_key: str | None = None
    tier3_fetch_enabled: bool = False

    @field_validator("jwt_secret", "pii_hash_salt", "worker_trigger_secret")
    @classmethod
    def _must_be_long_enough(cls, value: str, info) -> str:
        if len(value) < MIN_SECRET_LENGTH:
            raise ValueError(
                f"{info.field_name} must be at least {MIN_SECRET_LENGTH} characters. "
                f"Generate one with: openssl rand -hex 32"
            )
        if value.upper().startswith("CHANGE_ME"):
            raise ValueError(f"{info.field_name} still holds the placeholder value")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def require_ai(self) -> None:
        """Called by the AI router. Fails loudly rather than half-working."""
        if not self.ai_enabled:
            raise RuntimeError("AI_ENABLED is false")
        if not self.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        if not self.database_url_readonly:
            raise RuntimeError("DATABASE_URL_READONLY is not set; NL->SQL refuses to run without it")


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except Exception as exc:
        print("\n" + "=" * 72, file=sys.stderr)
        print("Norte no puede arrancar: falta configuración.", file=sys.stderr)
        print("=" * 72, file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print(
            "\nCopia .env.example a .env y rellena los valores vacíos.\n"
            "Para generar un secreto:  openssl rand -hex 32\n",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
