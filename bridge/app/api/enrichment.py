"""Enrichment API (docs/ENRICHMENT.md §4).

Cache-first candidate search per (name, source), read/apply of the resolved profile, and the
batch populate/auto-resolve operations. The image proxy lands in a later pass.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from bridge.app.api.deps import get_db
from bridge.app.cache.db import Database
from bridge.app.config import get_settings
from bridge.app.providers import PerformerData, ProviderError, get_provider, list_sources

router = APIRouter(prefix="/enrichment")


def _budget(source: str) -> int | None:
    """Soft credit ceiling for a metered source (None = unlimited)."""
    return get_settings().parse_bot_budget if source == "parsebot" else None


def _run_search(db: Database, name_id: int, source: str, refresh: bool = False) -> dict:
    """Cache-first search for one (name, source): cached candidates if already searched, else a
    live provider call (persisted, credit-guarded). Shared by /search and the batch ops."""
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


@router.get("/sources")
def sources() -> dict:
    return {"sources": list_sources()}


@router.get("/search")
def search(
    name_id: int = Query(...),
    source: str = Query(...),
    refresh: bool = False,
    db: Database = Depends(get_db),
) -> dict:
    """The search interface for one (name, source). Cache-first: returns cached candidates when the
    search has already run, else calls the provider and persists — one uniform response either way.
    Callers don't care whether the data came from the DB or live. `refresh=1` forces a live call."""
    return _run_search(db, name_id, source, refresh)


class SearchBatch(BaseModel):
    name_ids: list[int]
    source: str


@router.post("/search-batch")
def search_batch(body: SearchBatch, db: Database = Depends(get_db)) -> dict:
    """Populate: sequential cache-first search over the names against a source; resolves nothing.

    Sequential and synchronous (Stash Batch Search paradigm); credit-guarded per metered call.
    """
    out = []
    for nid in body.name_ids:
        try:
            r = _run_search(db, nid, body.source)
            out.append({"name_id": nid, "count": len(r["candidates"]),
                        "cached": r["cached"], "error": r["error"]})
        except HTTPException as e:
            out.append({"name_id": nid, "count": 0, "cached": False, "error": e.detail})
    return {"source": body.source, "results": out}


class UpdateBatch(BaseModel):
    name_ids: list[int]
    source: str
    exclude_fields: list[str] = []


@router.post("/update-batch")
def update_batch(body: UpdateBatch, db: Database = Depends(get_db)) -> dict:
    """Auto-resolve: for each name, ensure candidates (cache-first) then apply the best match's
    populated fields (minus excluded) onto the profile. For the unambiguous cases."""
    excl = set(body.exclude_fields)
    out = []
    for nid in body.name_ids:
        try:
            _run_search(db, nid, body.source)  # ensure candidates exist (cache-first)
        except HTTPException as e:
            out.append({"name_id": nid, "applied": 0, "error": e.detail})
            continue
        cands = db.list_candidates(nid, body.source)
        if not cands:
            out.append({"name_id": nid, "applied": 0, "error": None})
            continue
        top = max(cands, key=lambda c: c.get("score") or 0)  # best by score, else first
        data = PerformerData.from_dict(top["data"])
        fields = {
            k: {"value": v, "source": body.source}
            for k, v in data.populated_fields().items()
            if k not in excl
        }
        if fields:
            db.apply_enrichment_profile(nid, fields)
        out.append({"name_id": nid, "applied": len(fields), "error": None})
    return {"source": body.source, "results": out}


@router.get("/credits")
def credits(db: Database = Depends(get_db)) -> dict:
    spent = db.credits_spent("parsebot")
    return {"parsebot": {"spent": spent, "budget": get_settings().parse_bot_budget}}


@router.get("/profile")
def get_profile(name_id: int = Query(...), db: Database = Depends(get_db)) -> dict:
    return {"name_id": name_id, "profile": db.get_enrichment_profile(name_id)}


@router.get("/profiles")
def profiles(name_ids: str = Query(...), db: Database = Depends(get_db)) -> dict:
    """Lightweight per-name profile status for a page of names: field count + source(s)."""
    out: dict[int, dict] = {}
    for tok in name_ids.split(","):
        tok = tok.strip()
        if not tok:
            continue
        nid = int(tok)
        p = db.get_enrichment_profile(nid)
        if p:
            fs = p["field_sources"]
            images = p.get("images") or []
            out[nid] = {
                "fields": len(fs),
                "sources": sorted(set(fs.values())),
                "image": images[0] if images else None,
            }
    return {"profiles": out}


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
