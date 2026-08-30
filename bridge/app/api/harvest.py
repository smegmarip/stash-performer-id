"""Harvest trigger API. Runs synchronously for now (Phase 1); background jobs come later."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from bridge.app.api.deps import get_db
from bridge.app.cache.db import Database
from bridge.app.config import get_settings
from bridge.app.harvest.galleries import harvest_galleries
from bridge.app.harvest.paths import harvest_path
from bridge.app.harvest.scenes import harvest_scenes
from bridge.app.stash.client import StashClient

router = APIRouter(prefix="/harvest")


def _stash() -> StashClient:
    s = get_settings()
    key = s.stash_api_key.get_secret_value() if s.stash_api_key else None
    return StashClient(s.stash_url, key)


def _merge_counts(results: list[dict]) -> dict:
    """Sum the per-root count dicts of a multi-root harvest into one."""
    out: dict = {}
    for r in results:
        for k, v in r.items():
            out[k] = out.get(k, 0) + v
    return out


@router.post("/galleries")
def run_gallery_harvest(db: Database = Depends(get_db)) -> dict:
    # Scope to the TOP_FOLDER root(s) when configured (else the whole library is harvested).
    prefixes = get_settings().top_folders or [None]
    with _stash() as stash:
        return _merge_counts([harvest_galleries(db, stash, path_prefix=p) for p in prefixes])


@router.post("/scenes")
def run_scene_harvest(db: Database = Depends(get_db)) -> dict:
    # Scope to the TOP_FOLDER root(s) when configured (else the whole library is harvested).
    prefixes = get_settings().top_folders or [None]
    with _stash() as stash:
        return _merge_counts([harvest_scenes(db, stash, path_prefix=p) for p in prefixes])


class PathBody(BaseModel):
    root: str | None = None


@router.post("/path")
def run_path_harvest(body: PathBody, db: Database = Depends(get_db)) -> dict:
    roots = [body.root] if body.root else get_settings().top_folders
    if not roots:
        raise HTTPException(status_code=400, detail="no root or top_folder configured")
    return _merge_counts([harvest_path(db, r) for r in roots])
