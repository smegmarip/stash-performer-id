"""Name-DB API consumed by the viewer (Step 1) and the tagger page (Step 2)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from bridge.app.api.deps import get_db
from bridge.app.cache.db import Database

router = APIRouter()


@router.get("/audit/summary")
def audit_summary(db: Database = Depends(get_db)) -> dict:
    return db.summary()


@router.get("/names")
def list_names(
    status: str | None = None,
    q: str | None = None,
    sort: str = "name",
    order: str = "asc",
    limit: int = 100,
    offset: int = 0,
    enriched: str | None = None,
    db: Database = Depends(get_db),
) -> dict:
    """status ∈ {valid, invalid}; omit for all. Names are valid by default.
    enriched ∈ {matched, unmatched}; omit for all — matched = has a resolved enrichment profile."""
    return {
        "total": db.count_names(status=status, q=q, enriched=enriched),
        "names": db.list_names(
            status=status, q=q, sort=sort, order=order, limit=limit, offset=offset,
            enriched=enriched,
        ),
    }


class NameUpdate(BaseModel):
    valid: bool | None = None
    name: str | None = None
    disambiguation: str | None = None


@router.patch("/names/{name_id}")
def update_name(
    name_id: int, body: NameUpdate, db: Database = Depends(get_db)
) -> dict:
    # exclude_unset: only touch fields the client sent; drop explicit nulls.
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    row = db.update_name(name_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail="name not found")
    return row


class BulkValid(BaseModel):
    ids: list[int]
    valid: bool


@router.post("/names/set-valid")
def set_valid_bulk(body: BulkValid, db: Database = Depends(get_db)) -> dict:
    """Batch valid/invalid — invalidate (or restore) many names at once."""
    return {"updated": db.set_valid_bulk(body.ids, body.valid)}


class DirectName(BaseModel):
    name: str
    disambiguation: str = ""


@router.post("/names")
def add_name(body: DirectName, db: Database = Depends(get_db)) -> dict:
    return db.add_direct_name(body.name.strip(), body.disambiguation.strip())
