"""Typed service configuration (pydantic-settings).

Secrets are `SecretStr`; everything is env-driven (see .env.example).
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=False, extra="ignore"
    )

    # --- Service ---
    port: int = 15000
    public_base_url: str = "http://localhost:15000"

    # --- Stash connection (harvest + stash-box surface) ---
    stash_url: str = "http://localhost:9999"
    stash_api_key: SecretStr | None = None

    # --- Enrichment ---
    parse_bot_api_key: SecretStr | None = None

    # --- Harvest ---
    top_folder: str | None = None
    media_root: str = "/data"  # read-only media mount inside the container

    # --- Storage ---
    db_path: str = "/cache/stash-performer-id.sqlite"


@lru_cache
def get_settings() -> Settings:
    return Settings()
