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
    limit: int = 100,
    offset: int = 0,
    db: Database = Depends(get_db),
) -> list[dict]:
    """status ∈ {valid, invalid, untriaged}; omit for all."""
    return db.list_names(status=status, limit=limit, offset=offset)


class NameUpdate(BaseModel):
    valid: bool | None = None
    name: str | None = None
    disambiguation: str | None = None


@router.patch("/names/{name_id}")
def update_name(
    name_id: int, body: NameUpdate, db: Database = Depends(get_db)
) -> dict:
    fields = body.model_dump(exclude_unset=True)
    row = db.update_name(name_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail="name not found")
    return row


class DirectName(BaseModel):
    name: str
    disambiguation: str = ""


@router.post("/names")
def add_name(body: DirectName, db: Database = Depends(get_db)) -> dict:
    return db.add_direct_name(body.name.strip(), body.disambiguation.strip())
