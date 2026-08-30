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

    # Image hosts whose enrichment images block direct hotlinks (Referer checks) and so must be
    # refetched through /image-proxy before handing the URL to Stash. Comma-separated host suffixes
    # ("*" proxies every host). Uses public_base_url, so that must be reachable by Stash (and, for
    # in-UI previews, the browser) — the same requirement the stash-box endpoint already has.
    image_proxy_hosts: str = "thehandbook.com"

    # --- Enrichment ---
    parse_bot_api_key: SecretStr | None = None
    parse_bot_budget: int = 199  # soft credit ceiling for metered parse.bot calls
    flaresolverr_url: str | None = None  # e.g. http://flaresolverr:8191/v1 (Babepedia Cloudflare)

    # --- Harvest ---
    top_folder: str | None = None  # one root, or several separated by ':' (see top_folders)
    media_root: str = "/data"  # read-only media mount inside the container

    @property
    def top_folders(self) -> list[str]:
        """TOP_FOLDER as a list of roots (colon-separated); empty when unconfigured."""
        if not self.top_folder:
            return []
        return [p.strip() for p in self.top_folder.split(":") if p.strip()]

    # --- Storage ---
    db_path: str = "/cache/stash-performer-id.sqlite"


@lru_cache
def get_settings() -> Settings:
    return Settings()
