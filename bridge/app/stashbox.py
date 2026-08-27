"""stash-box-compatible GraphQL relay — the PerformerTagger subset.

This is the Stash *metadata provider*: registered under Settings -> Metadata Providers ->
Stash-Boxes with endpoint `${PUBLIC_BASE_URL}/graphql`, Stash's native PerformerTagger queries
`searchPerformer` / `findPerformer` / `me` here (server-side, snake_case SDL).

It serves the resolved, **source-agnostic** enrichment profile: one profile per name, keyed by
`names.id` (the same id the image tagger stamps as stash_id{endpoint:"stash-performer-id"}). The
enrichment source (babepedia/wikidata/…) is an internal detail and is never exposed — consumers
depend on the profile abstraction, not on which source filled a field (Dependency Inversion).

Read-only: enrichment happens only in the app's enrichment page; Stash pulls, never pushes.
Modelled on iafd-metadata-provider (schema.py / resolvers.py).
"""

from __future__ import annotations

import re
from enum import Enum

import strawberry

from bridge.app.api.deps import get_db
from bridge.app.api.imageproxy import proxy_image_url

SERVICE_NAME = "stash-performer-id"


# ─── stash-box enums (values match the stash-box SDL) ────────────────────────
@strawberry.enum
class GenderEnum(Enum):
    MALE = "MALE"
    FEMALE = "FEMALE"
    TRANSGENDER_MALE = "TRANSGENDER_MALE"
    TRANSGENDER_FEMALE = "TRANSGENDER_FEMALE"
    INTERSEX = "INTERSEX"
    NON_BINARY = "NON_BINARY"


@strawberry.enum
class EthnicityEnum(Enum):
    CAUCASIAN = "CAUCASIAN"
    BLACK = "BLACK"
    ASIAN = "ASIAN"
    INDIAN = "INDIAN"
    LATIN = "LATIN"
    MIDDLE_EASTERN = "MIDDLE_EASTERN"
    MIXED = "MIXED"
    OTHER = "OTHER"


@strawberry.enum
class EyeColorEnum(Enum):
    BLUE = "BLUE"
    BROWN = "BROWN"
    GREY = "GREY"
    GREEN = "GREEN"
    HAZEL = "HAZEL"
    RED = "RED"


@strawberry.enum
class HairColorEnum(Enum):
    BLONDE = "BLONDE"
    BRUNETTE = "BRUNETTE"
    BLACK = "BLACK"
    RED = "RED"
    AUBURN = "AUBURN"
    GREY = "GREY"
    BALD = "BALD"
    VARIOUS = "VARIOUS"
    WHITE = "WHITE"
    OTHER = "OTHER"


@strawberry.enum
class BreastTypeEnum(Enum):
    NATURAL = "NATURAL"
    FAKE = "FAKE"
    NA = "NA"


# ─── stash-box types ─────────────────────────────────────────────────────────
@strawberry.type
class User:
    name: str


@strawberry.type
class URL:
    url: str
    type: str  # deprecated in stash-box but still read by Stash's Go client


@strawberry.type
class Image:
    id: strawberry.ID
    url: str
    width: int | None = None
    height: int | None = None


@strawberry.type
class Measurements:
    band_size: int | None = None
    cup_size: str | None = None
    waist: int | None = None
    hip: int | None = None


@strawberry.type
class BodyModification:
    location: str
    description: str | None = None


@strawberry.type
class Performer:
    id: strawberry.ID
    name: str
    disambiguation: str | None = None
    aliases: list[str] = strawberry.field(default_factory=list)
    gender: GenderEnum | None = None
    merged_ids: list[strawberry.ID] = strawberry.field(default_factory=list)
    deleted: bool = False
    merged_into_id: strawberry.ID | None = None
    urls: list[URL] = strawberry.field(default_factory=list)
    images: list[Image] = strawberry.field(default_factory=list)
    birth_date: str | None = None
    death_date: str | None = None
    ethnicity: EthnicityEnum | None = None
    country: str | None = None
    eye_color: EyeColorEnum | None = None
    hair_color: HairColorEnum | None = None
    height: int | None = None
    measurements: Measurements | None = None
    breast_type: BreastTypeEnum | None = None
    career_start_year: int | None = None
    career_end_year: int | None = None
    tattoos: list[BodyModification] = strawberry.field(default_factory=list)
    piercings: list[BodyModification] = strawberry.field(default_factory=list)


# ─── enrichment_profile -> stash-box Performer ───────────────────────────────
def _to_enum(cls, value: str | None):
    if not value:
        return None
    key = value.strip().upper().replace(" ", "_").replace("-", "_")
    try:
        return cls[key]
    except KeyError:
        return None


def _int(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"\d+", value)
    return int(m.group()) if m else None


def _measurements(value: str | None) -> Measurements | None:
    # e.g. "34B-24-34" -> band 34, cup B, waist 24, hip 34.
    if not value:
        return None
    m = re.match(r"\s*(\d+)\s*([A-Za-z]+)?\s*-\s*(\d+)\s*-\s*(\d+)", value)
    if not m:
        return None
    band, cup, waist, hip = m.groups()
    return Measurements(
        band_size=int(band),
        cup_size=cup.upper() if cup else None,
        waist=int(waist),
        hip=int(hip),
    )


def _breast(value: str | None) -> BreastTypeEnum | None:
    if not value:
        return None
    low = value.strip().lower()
    if "natural" in low:
        return BreastTypeEnum.NATURAL
    if low in ("na", "n/a", "none"):
        return BreastTypeEnum.NA
    return BreastTypeEnum.FAKE


def _body_mods(value: str | None) -> list[BodyModification]:
    # Our profile stores a freeform string; stash-box wants structured entries.
    return [BodyModification(location=value.strip())] if value and value.strip() else []


def _to_performer(
    name_id: int, base_name: str, base_disambiguation: str | None, p: dict
) -> Performer:
    """Map a resolved enrichment_profile (dict) to a stash-box Performer, keyed by names.id.
    `base_name`/`base_disambiguation` are the name-record fallbacks when the profile omits them."""
    return Performer(
        id=strawberry.ID(str(name_id)),
        name=p.get("name") or base_name,
        disambiguation=p.get("disambiguation") or base_disambiguation or None,
        aliases=list(p.get("aliases") or []),
        gender=_to_enum(GenderEnum, p.get("gender")),
        urls=[URL(url=u, type="") for u in (p.get("urls") or []) if u],
        images=[
            # Route hotlink-protected hosts through the proxy so Stash can fetch them on create.
            Image(id=strawberry.ID(f"{name_id}#{i}"), url=proxy_image_url(u))
            for i, u in enumerate(p.get("images") or [])
            if u
        ],
        birth_date=p.get("birthdate"),
        death_date=p.get("death_date"),
        ethnicity=_to_enum(EthnicityEnum, p.get("ethnicity")),
        country=p.get("country"),
        eye_color=_to_enum(EyeColorEnum, p.get("eye_color")),
        hair_color=_to_enum(HairColorEnum, p.get("hair_color")),
        height=_int(p.get("height")),
        measurements=_measurements(p.get("measurements")),
        breast_type=_breast(p.get("fake_tits")),
        career_start_year=_int(p.get("career_start")),
        career_end_year=_int(p.get("career_end")),
        tattoos=_body_mods(p.get("tattoos")),
        piercings=_body_mods(p.get("piercings")),
    )


def _by_id(db, name_id: int) -> Performer | None:
    """The resolved profile for a name id, or None if the name has no profile."""
    profile = db.get_enrichment_profile(name_id)
    if not profile:
        return None
    name = db.get_name(name_id)
    if not name:
        return None
    return _to_performer(name_id, name["name"], name.get("disambiguation"), profile)


# ─── stash-box Scene surface (no-op) ─────────────────────────────────────────
# Stash's scene tagger queries a stash-box by fingerprint (FindScenesBySceneFingerprints /
# SearchScene / FindSceneByID). Our provider associates scenes by path/id via the *script* scraper
# (sceneByFragment), not by fingerprint, so it has no scene data to return. We still define these
# types and queries so those requests VALIDATE and return empty, instead of erroring with
# "Unknown type 'Scene'"/"Cannot query field 'findScenesBySceneFingerprints'" in the tagger UI.


@strawberry.enum
class FingerprintAlgorithm(Enum):
    MD5 = "MD5"
    OSHASH = "OSHASH"
    PHASH = "PHASH"


@strawberry.input
class FingerprintQueryInput:
    hash: str
    algorithm: FingerprintAlgorithm


@strawberry.type
class TagCategory:
    id: strawberry.ID
    name: str
    description: str | None = None


@strawberry.type
class Tag:
    id: strawberry.ID
    name: str
    description: str | None = None
    aliases: list[str] = strawberry.field(default_factory=list)
    category: TagCategory | None = None


@strawberry.type
class Studio:
    id: strawberry.ID
    name: str
    aliases: list[str] = strawberry.field(default_factory=list)
    urls: list[URL] = strawberry.field(default_factory=list)
    images: list[Image] = strawberry.field(default_factory=list)
    parent: Studio | None = None


@strawberry.type
class PerformerAppearance:
    performer: Performer
    as_: str | None = strawberry.field(name="as", default=None)


@strawberry.type
class Fingerprint:
    hash: str
    algorithm: FingerprintAlgorithm
    duration: int | None = None
    submissions: int | None = None


@strawberry.type
class Scene:
    id: strawberry.ID
    title: str | None = None
    code: str | None = None
    details: str | None = None
    director: str | None = None
    duration: int | None = None
    date: str | None = None
    urls: list[URL] = strawberry.field(default_factory=list)
    images: list[Image] = strawberry.field(default_factory=list)
    studio: Studio | None = None
    tags: list[Tag] = strawberry.field(default_factory=list)
    performers: list[PerformerAppearance] = strawberry.field(default_factory=list)
    fingerprints: list[Fingerprint] = strawberry.field(default_factory=list)


@strawberry.type
class Query:
    @strawberry.field
    def me(self) -> User:
        """Stash-Box identity / health probe."""
        return User(name=SERVICE_NAME)

    @strawberry.field(name="searchPerformer")
    def search_performer(self, term: str, limit: int | None = None) -> list[Performer]:
        """Curated profiles matching `term`. Stash also re-queries this with the stamped id on a
        stash-id refresh, so a numeric term resolves straight to that name's profile. Empty list
        when nothing matches (never an error)."""
        db = get_db()
        if term.strip().isdigit():
            p = _by_id(db, int(term.strip()))
            if p:
                return [p]
        rows = db.search_enriched_names(term, limit if (limit and limit > 0) else 25)
        out = [_to_performer(r["id"], r["name"], r.get("disambiguation"), prof)
               for r in rows
               if (prof := db.get_enrichment_profile(r["id"]))]
        return out

    @strawberry.field(name="findPerformer")
    def find_performer(self, id: strawberry.ID) -> Performer | None:
        return _by_id(get_db(), int(id)) if str(id).strip().isdigit() else None

    # --- Scene surface: no-op (see the "stash-box Scene surface" note above). ---
    @strawberry.field(name="findScenesBySceneFingerprints")
    def find_scenes_by_scene_fingerprints(
        self, fingerprints: list[list[FingerprintQueryInput]]
    ) -> list[list[Scene]]:
        # One (empty) match set per queried scene — no fingerprint DB to match against.
        return [[] for _ in fingerprints]

    @strawberry.field(name="searchScene")
    def search_scene(self, term: str) -> list[Scene]:
        return []

    @strawberry.field(name="findScene")
    def find_scene(self, id: strawberry.ID) -> Scene | None:
        return None
