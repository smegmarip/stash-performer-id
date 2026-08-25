"""Image scrape surface (Step 2: image -> performer, via the metadata provider).

Stash's stash-box client returns ErrNotSupported for image-by-fragment, so image association
goes through a *script* scraper: Stash pipes an image fragment to a transport script, which POSTs
it here. We resolve the image (by id, else file path) to its active name (Step 1) and return it as
a ScrapedImage — the shape Stash's `imageByFragment` expects.

See docs/IMAGE_TAGGER_FEASIBILITY.md §2.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from bridge.app.api.deps import get_db
from bridge.app.cache.db import Database

router = APIRouter(prefix="/scrape")

# Scalar enrichment_profile columns that map 1:1 onto Stash's ScrapedPerformer.
_SCRAPED_SCALARS = (
    "disambiguation", "gender", "birthdate", "death_date", "ethnicity", "country",
    "hair_color", "eye_color", "height", "weight", "measurements", "fake_tits",
    "penis_length", "circumcised", "career_start", "career_end", "tattoos", "piercings",
    "details",
)


def _merge_profile(performer: dict, profile: dict) -> None:
    """Merge a resolved enrichment profile onto the scraped performer (only populated fields).

    ScrapedPerformer expects `aliases` as a comma-joined string and `urls`/`images` as lists.
    A profile `name` (the enriched, canonical spelling) overrides the activated name.
    """
    if profile.get("name"):
        performer["name"] = profile["name"]
    for field in _SCRAPED_SCALARS:
        if profile.get(field):
            performer[field] = profile[field]
    if profile.get("aliases"):
        performer["aliases"] = ", ".join(profile["aliases"])
    if profile.get("urls"):
        performer["urls"] = profile["urls"]
    if profile.get("images"):
        performer["images"] = profile["images"]


class _File(BaseModel, extra="ignore"):
    path: str | None = None


class ImageFragment(BaseModel, extra="ignore"):
    """The subset of Stash's image fragment we key on (see script.go `imageInput`)."""

    id: str | None = None
    files: list[_File] = []


@router.post("/image")
def scrape_image(fragment: ImageFragment, db: Database = Depends(get_db)) -> dict:
    """Return the image's active name as a ScrapedImage `{performers: [...]}` (empty if none),
    enriched with the resolved profile when one exists.

    `remote_site_id` is the name-record id — the durable link the tagger stamps into the
    performer's `stash_ids` on create, so re-scrapes resolve to the same performer. Enrichment
    fields ride along when a profile has been resolved (docs/ENRICHMENT.md §6); no external calls.
    """
    paths = [f.path for f in fragment.files if f.path]
    active = db.lookup_active_name_for_image(stash_id=fragment.id, paths=paths)
    if not active:
        return {"performers": []}
    performer: dict = {"name": active["name"], "remote_site_id": str(active["name_id"])}
    if active.get("disambiguation"):
        performer["disambiguation"] = active["disambiguation"]
    profile = db.get_enrichment_profile(active["name_id"])
    if profile:
        _merge_profile(performer, profile)
    return {"performers": [performer]}
