"""
Centralized application configuration using Pydantic Settings.

All settings are loaded from environment variables (`.env` file supported).
Import ``settings`` directly for convenient access throughout the codebase:

    from app.core.config import settings
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment / ``.env`` file.

    Grouped by concern:

    * **OpenAI** – API key for GPT models (chat_agent, email_agent).
    * **Google OAuth** – file paths to OAuth credentials & token.
    * **Email** – organiser identity and Gmail polling frequency.
    * **Database** – SQLite file path.
    * **App** – runtime environment and CORS configuration.
    """

    # ── OpenAI ──────────────────────────────────────────────────────────────
    OPENAI_API_KEY: str
    """API key from platform.openai.com (required)."""

    # ── Google OAuth file paths ─────────────────────────────────────────────
    GOOGLE_CREDENTIALS_PATH: str = "app/credentials.json"
    """Path to the Google Cloud OAuth client-secret JSON file."""

    GOOGLE_TOKEN_PATH: str = "app/token.json"
    """Path where the OAuth refresh/access token is persisted."""

    # ── Email ──────────────────────────────────────────────────────────────
    ORGANIZER_EMAIL: str
    """Gmail address of the meeting organiser (required)."""

    GMAIL_POLL_INTERVAL_SECONDS: int = 30
    """How often (in seconds) the background poller checks for new emails."""

    # ── Database ────────────────────────────────────────────────────────────
    DATABASE_PATH: str = "logs.db"
    """Path to the SQLite database file."""

    # ── App ─────────────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    """Runtime environment: ``"development"`` or ``"production"``."""

    BASE_URL: str = "http://localhost:8000"
    """Public base URL of the API server (used in confirmation links, etc.)."""

    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"
    """Comma-separated list of allowed CORS origins."""

    LOG_LEVEL: str = "DEBUG"
    """Logging level for the application (DEBUG, INFO, WARNING, ERROR, CRITICAL)."""

    @property
    def cors_origins_list(self) -> list[str]:
        """Return CORS origins as a ``list[str]``."""
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# ── Singleton ─────────────────────────────────────────────────────────────────
@lru_cache()
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance (singleton)."""
    return Settings()


# Module-level convenience export – import this everywhere
settings = get_settings()
