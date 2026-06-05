"""Centralized, typed application configuration (12-factor).

Replaces scattered ``os.getenv`` lookups with a single validated ``Settings`` object.
Reads from process env and, if present, a local ``.env`` file. Secrets must come from
the environment in production — never from a committed file.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

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

    @property
    def cors_origins_list(self) -> list[str]:
        raw = (self.cors_allow_origins or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in {"production", "prod"}


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
