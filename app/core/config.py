"""Centralized, typed application configuration (12-factor).

Replaces scattered ``os.getenv`` lookups with a single validated ``Settings`` object.
Reads from process env and, if present, a local ``.env`` file. Secrets must come from
the environment in production — never from a committed file.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.platform.modes import AppMode, AppModePolicy, EgressPolicy, policy_for, resolve_mode
from app.utils.logger import setup_logging

logger = setup_logging(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── Server ───
    host: str = "127.0.0.1"
    port: int = 8000
    app_env: str = "development"
    # demo | self_hosted | production — see app.platform.modes. Falls back to APP_ENV mapping.
    app_mode: str | None = None

    # ─── Secrets at rest (comma-separated Fernet keys; first encrypts, all decrypt) ───
    dbw_secret_keys: str | None = None

    # ─── Local-first AI ───
    ollama_base_url: str = "http://127.0.0.1:11434"
    model_profile: str = (
        "auto"  # auto | fake | local-small | local-balanced | local-quality | <remote>
    )
    embedding_profile: str = "auto"  # auto | fake | local-bge-small | google
    egress_policy: str | None = None  # overrides the mode default (see EgressPolicy)

    # ─── Network policy for target connections ───
    network_allowlist: str | None = None  # comma-separated hosts / CIDRs permitted in strict mode
    trust_proxy_headers: bool = False  # honour X-Forwarded-For only behind a trusted proxy

    # ─── Observability ───
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "dbwhisper-api"
    metrics_enabled: bool = True

    # ─── Background worker ───
    worker_poll_interval_seconds: float = 2.0

    # ─── Project database (conversation memory + pgvector) ───
    postgres_connection_string: str | None = None

    # ─── CORS ───
    cors_allow_origins: str = "*"

    # ─── Embeddings ───
    embedding_provider: str = "google"

    # ─── LLM provider keys (presence drives the fallback chain) ───
    gemini_api_key: str | None = None
    google_api_key: str | None = None
    groq_api_key: str | None = None
    openai_api_key: str | None = None
    openrouter_api_key: str | None = None
    deepseek_api_key: str | None = None
    anthropic_api_key: str | None = None

    # ─── Auth (API-key gate for powerful endpoints) ───
    api_auth_tokens: str | None = None  # comma-separated; presence enables auth
    auth_required: bool = False  # force-require even outside production
    gate_query_endpoint: bool = False  # also require an API key on /query

    # ─── User auth (server-side sessions; multi-tenancy) ───
    user_auth_enabled: bool = False  # gate /query + /schemas/* behind login when True
    session_ttl_seconds: int = 1_209_600  # 14 days
    session_cookie_secure: bool | None = None  # None → secure in production
    # None → enforced in production, opt-in in self-hosted, not applicable in demo
    # (which has no login, therefore no ambient authority). See app/security/csrf.py.
    csrf_enforced: bool | None = None

    # ─── Rate limiting ───
    # Per-IP token bucket held in this process's memory. That is a real limit for a single
    # instance and NOT a limit across replicas: two instances each admit the full burst. See
    # app/security/ratelimit.py, which is written against an interface a shared-store limiter
    # can implement when a deployment needs one.
    rate_limit_enabled: bool = True
    rate_limit_burst: int = 30
    rate_limit_per_sec: float = 0.5
    query_rate_limit_burst: int = 8
    query_rate_limit_per_sec: float = 0.1

    # ─── Logging / observability ───
    log_level: str = "INFO"
    log_json: bool | None = None  # None → JSON when in production
    sentry_dsn: str | None = None

    # ─── Query-result cache ───
    query_cache_enabled: bool = True
    query_cache_ttl: int = 300

    @property
    def cors_origins_list(self) -> list[str]:
        raw = (self.cors_allow_origins or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in {"production", "prod"}

    @property
    def auth_tokens_list(self) -> list[str]:
        raw = (self.api_auth_tokens or "").strip()
        return [t.strip() for t in raw.split(",") if t.strip()]

    @property
    def effective_auth_required(self) -> bool:
        """Require an API key when explicitly forced, or whenever running in production.

        Fail-closed in production: if no API_AUTH_TOKENS are configured, the gated endpoints
        return 503 until the operator provides one.
        """
        return self.auth_required or self.is_production

    @property
    def effective_log_json(self) -> bool:
        return self.is_production if self.log_json is None else self.log_json

    @property
    def cookie_secure(self) -> bool:
        """Session cookies are Secure in production/demo unless explicitly overridden."""
        if self.session_cookie_secure is None:
            return self.is_production or self.mode_policy.cookie_secure
        return self.session_cookie_secure

    # ─── Application mode (typed policy; see app.platform.modes) ───
    @property
    def mode(self) -> AppMode:
        return resolve_mode(self.app_mode, self.app_env)

    @property
    def mode_policy(self) -> AppModePolicy:
        return policy_for(self.mode)

    @property
    def effective_egress_policy(self) -> EgressPolicy:
        raw = (self.egress_policy or "").strip().upper()
        return EgressPolicy(raw) if raw else self.mode_policy.default_egress_policy

    @property
    def secret_keys_list(self) -> list[str]:
        return [k.strip() for k in (self.dbw_secret_keys or "").split(",") if k.strip()]

    @property
    def network_allowlist_list(self) -> list[str]:
        return [h.strip() for h in (self.network_allowlist or "").split(",") if h.strip()]


def _normalize_google_key(settings: Settings) -> None:
    """Allow GEMINI_API_KEY as an alias for GOOGLE_API_KEY.

    ``langchain-google-genai`` (chat + embeddings) reads ``GOOGLE_API_KEY``. Many users
    set ``GEMINI_API_KEY`` instead, so mirror it into the environment if needed.
    """
    if not os.environ.get("GOOGLE_API_KEY"):
        gemini = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY")
        if gemini:
            os.environ["GOOGLE_API_KEY"] = gemini
            logger.debug("Aliased GEMINI_API_KEY -> GOOGLE_API_KEY for google-genai clients")


@lru_cache
def get_settings() -> Settings:
    """Return the cached, validated application settings."""
    settings = Settings()
    _normalize_google_key(settings)
    return settings
