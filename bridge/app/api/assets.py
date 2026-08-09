"""Asset activation API (Step 1: name -> asset). Gallery-level for now."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from bridge.app.api.deps import get_db
from bridge.app.cache.db import Database

router = APIRouter(prefix="/assets")


@router.get("/galleries")
def list_galleries(db: Database = Depends(get_db)) -> list[dict]:
    return db.list_gallery_assets()


class Activate(BaseModel):
    name_id: int
    source_level: str = "gallery"


@router.post("/{asset_id}/activate")
def activate(asset_id: int, body: Activate, db: Database = Depends(get_db)) -> dict:
    db.activate_name(asset_id, body.name_id, body.source_level, origin_asset_id=asset_id)
    return {"ok": True}


@router.delete("/{asset_id}/activation")
def deactivate(asset_id: int, db: Database = Depends(get_db)) -> dict:
    db.deactivate_asset(asset_id)
    return {"ok": True}
