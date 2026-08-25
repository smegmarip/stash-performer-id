"""Enrichment API (docs/ENRICHMENT.md §4).

Cache-first candidate search per (name, source), and read/apply of the resolved profile. Batch
endpoints, the credit ledger surface, and the image proxy land in a later pass.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from bridge.app.api.deps import get_db
from bridge.app.cache.db import Database
from bridge.app.config import get_settings
from bridge.app.providers import ProviderError, get_provider, list_sources

router = APIRouter(prefix="/enrichment")


def _budget(source: str) -> int | None:
    """Soft credit ceiling for a metered source (None = unlimited)."""
    return get_settings().parse_bot_budget if source == "parsebot" else None


@router.get("/sources")
def sources() -> dict:
    return {"sources": list_sources()}


@router.get("/candidates")
def candidates(
    name_id: int = Query(...),
    source: str = Query(...),
    refresh: bool = False,
    db: Database = Depends(get_db),
) -> dict:
    """Cache-first: return cached candidates when the (name, source) search has run, else call the
    provider, persist the results, and return them. `refresh=1` forces a live call."""
    provider = get_provider(source)
    if provider is None:
        raise HTTPException(status_code=400, detail=f"unknown source '{source}'")

    if not refresh and db.has_enrichment_search(name_id, source):
        return {"name_id": name_id, "source": source, "cached": True, "error": None,
                "candidates": db.list_candidates(name_id, source)}

    name = db.get_name(name_id)
    if not name:
        raise HTTPException(status_code=404, detail="name not found")
    term = name["name"]

    # Credit guard: refuse a live metered call once the soft budget is spent (degrade to cache).
    budget = _budget(source)
    if provider.metered and budget is not None and db.credits_spent(source) >= budget:
        return {"name_id": name_id, "source": source, "cached": False,
                "error": f"credit budget reached ({db.credits_spent(source)}/{budget})",
                "candidates": db.list_candidates(name_id, source)}

    error: str | None = None
    try:
        results = provider.search(term)
        db.replace_candidates(
            name_id,
            source,
            [{"source_entity_id": r.source_entity_id, "data": r.to_dict(), "score": r.score}
             for r in results],
        )
        count = len(results)
        if provider.metered:
            db.add_credit(source, 1, name_id)
    except ProviderError as e:
        error, count = str(e), 0

    db.record_enrichment_search(name_id, source, term, count, error)
    return {"name_id": name_id, "source": source, "cached": False, "error": error,
            "candidates": db.list_candidates(name_id, source)}


@router.get("/credits")
def credits(db: Database = Depends(get_db)) -> dict:
    spent = db.credits_spent("parsebot")
    return {"parsebot": {"spent": spent, "budget": get_settings().parse_bot_budget}}


@router.get("/profile")
def get_profile(name_id: int = Query(...), db: Database = Depends(get_db)) -> dict:
    return {"name_id": name_id, "profile": db.get_enrichment_profile(name_id)}


class ApplyProfile(BaseModel):
    name_id: int
    # {column: {"value": ..., "source": ...}} — only populated fields are sent.
    fields: dict[str, dict]


@router.post("/profile")
def apply_profile(body: ApplyProfile, db: Database = Depends(get_db)) -> dict:
    if not db.get_name(body.name_id):
        raise HTTPException(status_code=404, detail="name not found")
    profile = db.apply_enrichment_profile(body.name_id, body.fields)
    return {"name_id": body.name_id, "profile": profile}
