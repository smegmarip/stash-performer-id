"""Harvest trigger API. Runs synchronously for now (Phase 1); background jobs come later."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from bridge.app.api.deps import get_db
from bridge.app.cache.db import Database
from bridge.app.config import get_settings
from bridge.app.harvest.galleries import harvest_galleries
from bridge.app.harvest.paths import harvest_path
from bridge.app.stash.client import StashClient

router = APIRouter(prefix="/harvest")


def _stash() -> StashClient:
    s = get_settings()
    key = s.stash_api_key.get_secret_value() if s.stash_api_key else None
    return StashClient(s.stash_url, key)


@router.post("/galleries")
def run_gallery_harvest(db: Database = Depends(get_db)) -> dict:
    # Scope to TOP_FOLDER when configured (else the whole library is harvested).
    with _stash() as stash:
        return harvest_galleries(db, stash, path_prefix=get_settings().top_folder)


class PathBody(BaseModel):
    root: str | None = None


@router.post("/path")
def run_path_harvest(body: PathBody, db: Database = Depends(get_db)) -> dict:
    root = body.root or get_settings().top_folder
    if not root:
        raise HTTPException(status_code=400, detail="no root or top_folder configured")
    return harvest_path(db, root)
