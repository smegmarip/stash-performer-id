"""Source-neutral performer DTO.

The standalone subset of Stash's performer schema (`ScrapedPerformer` / `PerformerCreateInput`),
excluding entity references (tags, stash_ids) and management/deprecated fields, so every field is a
scalar or list-of-scalar and a candidate/profile is self-contained. See docs/ENRICHMENT.md §3.
"""

from dataclasses import asdict, dataclass, field, fields

# The performer metadata fields (everything except source/score bookkeeping). Used to decide
# "only populated fields" for the UI/apply, and as the enrichment_profile column set.
PROFILE_FIELDS: tuple[str, ...] = (
    "name",
    "disambiguation",
    "aliases",
    "gender",
    "birthdate",
    "death_date",
    "ethnicity",
    "country",
    "hair_color",
    "eye_color",
    "height",
    "weight",
    "measurements",
    "fake_tits",
    "penis_length",
    "circumcised",
    "career_start",
    "career_end",
    "tattoos",
    "piercings",
    "details",
    "urls",
    "images",
)

# Fields that hold a list of scalars (serialized as JSON in the DB).
LIST_FIELDS: frozenset[str] = frozenset({"aliases", "urls", "images"})


@dataclass
class PerformerData:
    source: str
    source_entity_id: str
    name: str
    disambiguation: str | None = None
    aliases: list[str] = field(default_factory=list)
    gender: str | None = None
    birthdate: str | None = None
    death_date: str | None = None
    ethnicity: str | None = None
    country: str | None = None
    hair_color: str | None = None
    eye_color: str | None = None
    height: str | None = None  # centimetres
    weight: str | None = None
    measurements: str | None = None
    fake_tits: str | None = None
    penis_length: str | None = None
    circumcised: str | None = None
    career_start: str | None = None
    career_end: str | None = None
    tattoos: str | None = None
    piercings: str | None = None
    details: str | None = None
    urls: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    score: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PerformerData":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def populated_fields(self) -> dict:
        """Only the profile fields that actually carry data (non-null / non-empty).

        `PerformerData` is the superset of possible fields; the resolve modal and apply flows show
        and write only these (docs/ENRICHMENT.md §5.3).
        """
        out: dict = {}
        for name in PROFILE_FIELDS:
            value = getattr(self, name)
            if value not in (None, "", [], {}):
                out[name] = value
        return out
