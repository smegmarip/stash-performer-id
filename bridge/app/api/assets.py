"""Asset activation API (Step 1: name -> asset), across gallery/folder/file scopes."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from bridge.app.api.deps import get_db
from bridge.app.cache.db import Database

router = APIRouter(prefix="/assets")

_SCOPES = {"gallery", "folder", "file"}


_ENTITY_TYPES = {"image", "scene"}


@router.get("")
def list_assets(
    type: str = Query("gallery"),
    q: str | None = None,
    sort: str = "path",
    order: str = "asc",
    assigned: str | None = None,  # "assigned" | "unassigned" | None (all)
    entity_type: str | None = None,  # narrow the file scope: "image" | "scene"
    limit: int = 100,
    offset: int = 0,
    db: Database = Depends(get_db),
) -> dict:
    if type not in _SCOPES:
        raise HTTPException(status_code=400, detail=f"type must be one of {sorted(_SCOPES)}")
    if entity_type is not None and entity_type not in _ENTITY_TYPES:
        raise HTTPException(
            status_code=400, detail=f"entity_type must be one of {sorted(_ENTITY_TYPES)}"
        )
    return {
        "total": db.count_assets(type, q=q, assigned=assigned, entity_type=entity_type),
        "assets": db.list_assets(
            type, q=q, sort=sort, order=order, assigned=assigned,
            limit=limit, offset=offset, entity_type=entity_type,
        ),
    }


class Activate(BaseModel):
    name_id: int
    source_level: str = "gallery"


@router.post("/{asset_id}/activate")
def activate(asset_id: int, body: Activate, db: Database = Depends(get_db)) -> dict:
    affected = db.activate_name(
        asset_id, body.name_id, body.source_level, origin_asset_id=asset_id
    )
    return {"ok": True, "affected": affected}


@router.delete("/{asset_id}/activation")
def deactivate(asset_id: int, db: Database = Depends(get_db)) -> dict:
    return {"ok": True, "affected": db.deactivate_asset(asset_id)}


@router.post("/{asset_id}/ignore")
def ignore(asset_id: int, db: Database = Depends(get_db)) -> dict:
    """Mark an asset (and its subtree) ignored — removed from triage and scraping."""
    return {"ok": True, "affected": db.ignore_asset(asset_id, True)}


@router.delete("/{asset_id}/ignore")
def unignore(asset_id: int, db: Database = Depends(get_db)) -> dict:
    return {"ok": True, "affected": db.ignore_asset(asset_id, False)}


class BulkIgnore(BaseModel):
    ids: list[int]
    ignored: bool = True


@router.post("/ignore")
def ignore_bulk(body: BulkIgnore, db: Database = Depends(get_db)) -> dict:
    return {"ok": True, "affected": db.ignore_assets_bulk(body.ids, body.ignored)}
