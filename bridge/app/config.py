"""Typed service configuration (pydantic-settings).

Secrets are `SecretStr`; everything is env-driven (see .env.example).
"""

from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_PORT = 15000  # single source of truth for the service port default


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=False, extra="ignore"
    )

    # --- Service ---
    port: int = DEFAULT_PORT
    public_base_url: str | None = None  # defaults to http://localhost:{port}

    @model_validator(mode="after")
    def _default_public_base_url(self) -> "Settings":
        if not self.public_base_url:
            self.public_base_url = f"http://localhost:{self.port}"
        return self

    # --- Stash connection (harvest + stash-box surface) ---
    stash_url: str = "http://localhost:9999"
    stash_api_key: SecretStr | None = None

    # --- Enrichment ---
    parse_bot_api_key: SecretStr | None = None
    parse_bot_budget: int = 199  # soft credit ceiling for metered parse.bot calls

    # --- Harvest ---
    top_folder: str | None = None
    media_root: str = "/data"  # read-only media mount inside the container

    # --- Storage ---
    db_path: str = "/cache/stash-performer-id.sqlite"


@lru_cache
def get_settings() -> Settings:
    return Settings()
