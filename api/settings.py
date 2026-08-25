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
    # Browser origins allowed to call the API directly (file uploads do; the
    # rest goes through the web's own proxy). Comma-separated exact origins,
    # plus an optional regex for the ones that change with every deploy -
    # Vercel's preview URLs. Production values live in render.yaml.
    cors_origins: str = "http://localhost:3000"
    cors_origin_regex: str | None = None
    # The origin an OUTSIDE caller uses to reach this API - what n8n, Make or
    # Zapier will POST to. Behind a proxy the request's own host is the internal
    # one (`http://api:8000`), and a webhook URL built from it is unusable by the
    # only party that matters. That URL is shown to the operator exactly once, so
    # it cannot be allowed to be wrong. Unset falls back to the request's host,
    # which is right in local development and nowhere else.
    public_api_url: str | None = None
    # Whether `X-Forwarded-For` is believed at all. True behind Render, a load
    # balancer or any reverse proxy that appends the caller's address; set it
    # to false when the API is reached DIRECTLY, or a caller can pick their own
    # rate-limit bucket by writing the header themselves.
    trust_proxy_headers: bool = True
    # The web app fronts the API through its own server (app/api/backend). From
    # here every request then arrives from ONE address - the web server's - and
    # the real caller travels in `X-Client-IP`. That header is believed only
    # when `X-Proxy-Secret` matches this value; anyone reaching the API directly
    # cannot forge it. Unset = the header is ignored and the last hop is used.
    proxy_shared_secret: str | None = None
    max_upload_mb: int = 25
    ingest_max_concurrency: int = 4
    rate_limit_auth_per_minute: int = 10
    rate_limit_ingest_per_minute: int = 30
    upload_dir: str = "uploads"
    log_level: str = "INFO"
    environment: str = "development"

    # --- ai ---
    ai_enabled: bool = False
    gemini_api_key: str | None = None
    ai_model: str = "gemini-3.6-flash"
    # Cap, not switch. Gemini 3.x refuses a budget of 0 with a 400, and thinking
    # tokens are billed against max_output_tokens, so an uncapped budget lets a
    # short answer spend its whole ceiling reasoning and return empty.
    ai_thinking_budget: int = 128
    ai_daily_token_budget: int = 200_000
    ai_request_timeout_seconds: int = 45

    # --- worker ---
    worker_enabled: bool = True
    fx_provider_url: str = "https://open.er-api.com/v6/latest"

    # --- plans (migration 048) ---
    # WhatsApp number of the advisor for the custom plan, digits with country
    # code (e.g. 573001234567). None hides the button.
    advisor_whatsapp: str | None = None
    trial_days: int = 30
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

    @field_validator("public_api_url")
    @classmethod
    def _must_be_an_origin(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        cleaned = value.strip().rstrip("/")
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError(
                "PUBLIC_API_URL must start with http:// or https:// "
                "(e.g. https://api.dataeffi.co)"
            )
        return cleaned

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def public_url_for(self, path: str, *, fallback_base: str) -> str:
        """Absolute URL an outside caller can actually reach.

        `fallback_base` is the request's own base, used only when PUBLIC_API_URL
        is unset. Anything handed to a third party - a webhook URL above all -
        must go through here rather than reading the request host directly.
        """
        base = self.public_api_url or fallback_base.rstrip("/")
        return f"{base}/{path.lstrip('/')}"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def require_ai(self) -> None:
        """Called by the AI router. Fails loudly rather than half-working."""
        if not self.ai_enabled:
            raise RuntimeError("AI_ENABLED is false")
        if not self.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        if not self.database_url_readonly:
            raise RuntimeError("DATABASE_URL_READONLY is not set; NL->SQL refuses to run without it")


@lru_cache
def get_settings() -> Settings:
    try:
        # Every required field is read from the environment by pydantic-settings.
        return Settings()  # type: ignore[call-arg]
    except Exception as exc:
        print("\n" + "=" * 72, file=sys.stderr)
        print("Data Effi no puede arrancar: falta configuración.", file=sys.stderr)
        print("=" * 72, file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print(
            "\nCopia .env.example a .env y rellena los valores vacíos.\n"
            "Para generar un secreto:  openssl rand -hex 32\n",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
