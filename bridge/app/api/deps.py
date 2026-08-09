"""FastAPI dependencies. The service owns a single Database instance, created lazily so
importing the app (e.g. in tests that don't touch the DB) never opens a file.
"""

from functools import lru_cache

from bridge.app.cache.db import Database
from bridge.app.config import get_settings


@lru_cache
def get_db() -> Database:
    return Database(get_settings().db_path)
