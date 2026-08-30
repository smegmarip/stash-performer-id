# Enrichment — design

Multi-field performer metadata, layered on top of the name→asset relationship. Expands the
model from **asset → name** to **asset → name → enriched profile**, where the name becomes a
disambiguated field of a larger object rather than the whole reference. This is the concrete
design for DESIGN §7 (which framed enrichment as a stash-box surface); it supersedes that
framing where they differ.

Grounded in Stash's native **PerformerTagger** (`/performers?disp=3`): per-entity search →
candidate grid → a field-level apply/override modal, plus batch search/update and an
active-source selector.

---

## 1. Principles

- **Name stays the reference key.** `names.id` is already the stable, durable identifier for a
  name — no disambiguation needed. `disambiguation` is a plain field of the profile that exists
  only for Stash compatibility and user convenience, never part of the id. No schema refactor —
  enrichment is a **new set of tables with a FK to `names.id`**, layered on the existing
  name↔asset relationship.
- **Enrichment never touches the scrape/association path.** It is precomputed and persisted; the
  `imageByFragment` scraper only **reads** the resolved profile (a DB join) and never calls an
  external API. So batch "Scrape All" in the image tagger costs zero credits.
- **Sources are pluggable.** Babepedia (free, rich bio — primary for this domain), Wikidata
  (free bio), and parse.bot/The Handbook (metered, image + socials) today; the `Provider` seam
  is open to more. Source selection picks _which API runs a search_ — there is **no cross-source
  merge policy** (see §5.3).
- **Search results and profiles are persisted separately.** Every candidate from every search is
  cached; the resolved profile is composed from them by explicit, field-level user choices.
- **Credit-safe.** Cache-first, sequential-async requests (Stash Batch Search paradigm),
  Wikidata-first (free), parse.bot metered behind a ledger; degrade gracefully when capped.
- **Reuse the scene tagger's tagging framework — do not reinvent it.** The candidate grid, the
  per-field `IncludeButton`/`OptionalField` ✓/✕ override, the image carousel, and the
  create/update result modal already exist in Stash's Tagger (`components/Tagger/**`). Port that
  component decomposition and interaction model into the enrichment UI and wire our data layer to
  it; the only thing we author is the data plumbing, not a new tagging UI. (Where a component is
  reachable natively — e.g. `PerformerSelect` via pluginApi — use it directly; where it is internal,
  port its structure + reuse the tagger CSS classes rather than redesigning, as the image-tagger
  page already does with `search-item`/`tagger-container`.)

---

## 2. Data model (two tiers, FK → `names`)

**Cache tier — every candidate from every search, kept forever (dedup, re-usable):**

```
enrichment_search        -- marks that a (name, source) search ran (distinguishes
  id PK                     0-results from never-searched, so cache-first is exact)
  name_id      FK names(id) ON DELETE CASCADE
  source       TEXT        -- 'wikidata' | 'parsebot' | ...
  query        TEXT        -- the term sent
  result_count INTEGER
  error        TEXT
  searched_at  TEXT
  UNIQUE(name_id, source)

enrichment_candidate     -- one row per matched entity a search returned
  id PK
  name_id          FK names(id) ON DELETE CASCADE
  source           TEXT
  source_entity_id TEXT    -- QID / parse.bot profile id / ...
  data             TEXT     -- JSON PerformerData (source-neutral, §3)
  score            REAL     -- source rank/confidence, optional
  fetched_at       TEXT
  UNIQUE(name_id, source, source_entity_id)
```

**Resolved tier — one composed profile per name, what the scraper reads:**

```
enrichment_profile           -- same standalone fields as PerformerData (§3), each nullable,
  name_id PK   FK names(id)      applied/overridden field-by-field
  name TEXT, disambiguation TEXT, aliases TEXT(json),
  gender TEXT, birthdate TEXT, death_date TEXT, ethnicity TEXT, country TEXT,
  hair_color TEXT, eye_color TEXT, height TEXT, weight TEXT, measurements TEXT,
  fake_tits TEXT, penis_length TEXT, circumcised TEXT, career_start TEXT, career_end TEXT,
  tattoos TEXT, piercings TEXT, details TEXT, urls TEXT(json), images TEXT(json),
  field_sources TEXT(json)   -- per-field provenance {field: source} for display
  updated_at TEXT
```

**Credit ledger (append-only), for the metered source(s):**

```
enrichment_credit_ledger
  id PK, source TEXT, cost INTEGER, name_id, at TEXT
```

---

## 3. Provider seam (extensible)

A source-neutral DTO and a protocol; resolvers/UI depend only on the DTO.

`PerformerData` mirrors **Stash's full performer schema** (`ScrapedPerformer` / `PerformerCreateInput`),
excluding every field that references a Stash entity or is Stash-management state, so each field is
**standalone** (scalar, list-of-scalar, or — for `custom_fields` — a flat `{name: scalar}` map).
Excluded: `tags`/`tag_ids`, `stash_ids`, `favorite`, `rating100`, `ignore_auto_tag`, and the
deprecated `url`/`twitter`/`instagram`/`career_length`/`image` (folded into `urls`/`images`).

`custom_fields` normalizes source data that has no home in the performer schema, as a flat map
of scalar values with **source-prefixed keys** (`ncaa_position`). The map stays provider-side —
no Stash pull surface can carry it (`ScrapedPerformer` has no map field; the stash-box client's
Go mapping is fixed), and we do not push Stash performer custom fields via mutations. It lives
in the enrichment profile and the viewer.

```python
@dataclass
class PerformerData:            # standalone subset of Stash's performer schema
    source: str
    source_entity_id: str
    # identity
    name: str
    disambiguation: str | None
    aliases: list[str]         # -> alias_list
    # bio
    gender: str | None
    birthdate: str | None
    death_date: str | None
    ethnicity: str | None
    country: str | None
    hair_color: str | None
    eye_color: str | None
    height: str | None         # cm (-> height_cm)
    weight: str | None
    measurements: str | None
    fake_tits: str | None
    penis_length: str | None
    circumcised: str | None
    career_start: str | None
    career_end: str | None
    tattoos: str | None
    piercings: str | None
    details: str | None
    # media / links
    urls: list[str]
    images: list[str]          # source image URLs (served via the /images proxy)
    # meta
    score: float | None

class Provider(Protocol):
    id: str                    # 'wikidata' | 'parsebot'
    metered: bool
    def search(self, term: str) -> list[PerformerData]: ...
```

`enrichment_profile` (§2) carries the same standalone columns; `enrichment_candidate.data` is a
serialized `PerformerData`.

- **`BabepediaProvider`** (free; **primary bio source** for this domain): the full performer
  superset — birthdate/death, country, ethnicity, hair/eye, height, weight, measurements,
  fake_tits, tattoos, piercings, aliases, details, socials, image gallery. Two-step (ajax-search →
  top-5 babe pages), lxml XPath ported from CommunityScrapers, cloudscraper for Cloudflare.
  `source_entity_id` = babe slug.
- **`WikidataProvider`** (free): bio via claims — aliases, P569 birthdate, P21 gender, P27 country,
  P1884 hair, P2048 height, P18 image, official/social URLs. `source_entity_id` = QID.
- **`ParseBotProvider`** (metered: **199 test credits**): The Handbook `search_profiles` — a
  talent-contact directory, so **image + profile URL** (type → disambiguation, social_reach →
  score) only, no bio. `source_entity_id` = profile id.
- **`NcaaProvider`** (free): stats.ncaa.org — the only comprehensive NCAA athlete index (~3M
  records, every sport/division back to the '90s). DataTables JSON search → recency-sorted
  candidates (name, teams, career span, gender inferred from sport), then the player page's bio
  `<dl>` → height (ft-in → cm), hometown (→ `country` for US states), and the schema-less rest
  (position/class/jersey/hometown/high-school/teams) into `custom_fields` as `ncaa_*` keys.
  No birthdate or photos (team-roster scraping is a planned follow-up). Akamai
  is cleared via curl_cffi Chrome TLS impersonation + an inline proof-of-work solve (FlareSolverr
  doesn't apply — it targets Cloudflare). `source_entity_id` = player id.
- A `providers` registry exposes the list to the viewer's active-source selector; adding a source
  = one new `Provider` impl + registry entry.

---

## 4. Service API

- `GET  /enrichment/sources` → `[{id, label, metered}]`
- `GET  /enrichment/search?name_id=&source=` → the search interface for one (name, source).
  Cache-first: if `enrichment_search` has a row, return cached `enrichment_candidate`s; else call
  `Provider.search`, persist search+candidates, return them — one uniform response either way.
  (`?refresh=1` forces a live call.)
- `GET  /enrichment/profile?name_id=` → the resolved `enrichment_profile` (or null)
- `POST /enrichment/profile` `{name_id, fields:{field: {value, source}}}` → apply the ✓ fields
  onto the profile (override), record `field_sources`, return the updated profile
- `POST /enrichment/search-batch` `{name_ids[], source}` → **populate**: sequential-async,
  cache-first, credit-guarded; streams/polls progress. Resolves nothing.
- `POST /enrichment/update-batch` `{name_ids[], source, exclude_fields[]}` → **auto-resolve**:
  for each name, apply the top candidate onto the profile (honoring excluded fields).
- `GET  /enrichment/credits` → `{source: {spent, budget}}`
- `GET  /images/<sha>` → proxy/cache for source images (so the browser/Stash needs no external host)

---

## 5. Flows

### 5.1 Individual (disambiguation)

Enrichment view → pick a **valid** name → **Search** (active source) → **candidate grid** (thumb +
name + disambiguation, from cache or live) → click the right candidate → **resolve modal**:
per-field **✓/✕** toggles + an **image carousel** (pick 1 of N, or exclude) → **Save** writes the
✓ fields onto `enrichment_profile`. Re-run against another source and ✓ only the fields you want
from it — the profile accumulates field-by-field.

### 5.2 Batch — two modes (both, mirroring the native pair)

- **Search All (populate)** — _primary_. Sequential search of the selected names against the
  active source; saves all candidates to cache; **resolves nothing** (disambiguation deferred to
  manual pick). Safe and re-runnable.
- **Update (auto-resolve)** — _secondary_, like "Batch Update Performers". Applies each name's top
  match onto its profile, honoring the excluded-fields default. For the unambiguous cases.

### 5.3 Field override semantics — **no precedence config**

The profile is a standalone record. Source selection only chooses which API a search runs against;
candidates from all searches are cached separately. Applying a candidate writes its **✓-checked
fields onto the profile**, overriding just those and leaving the rest. "Precedence" is simply
whatever the user last toggled in — there is no automatic merge policy. A global **excluded-fields**
default (e.g. Name) is bypassed on every apply, matching the native tagger's Configuration panel.

**Only populated fields are exposed.** `PerformerData` defines the *superset* of possible fields,
but a candidate rarely fills them all. The resolve modal (and the plugin create/update) lists
**only the fields the selected candidate actually returned** (non-null/non-empty) — empty fields
are omitted from the UI and never written, exactly as the native tagger shows only the fields a
candidate provided. Likewise `update-batch` only writes populated fields. Having a column in the
schema never forces it into the UI.

---

## 6. Scrape integration (Step 2 identify + associate)

`/scrape/image` (unchanged trigger) now returns a **full `ScrapedPerformer`** built from
`enrichment_profile` for the image's active name:

- `stored_id` — the matched local performer, resolved by the provider: `findPerformers(
stash_id_endpoint {endpoint:"stash-performer-id", stash_id:name_id})` → else exact/alias name.
- `remote_site_id` = `name_id` (the durable, spelling-independent link; §3 / DESIGN §8).
- all enriched fields (gender, country, urls, images, …) when a profile exists; **name-only** when
  it doesn't (enrichment is optional).

The image tagger then creates a **fully-populated** performer (`performerCreate` with every field +
`stash_ids:[{endpoint:"stash-performer-id", stash_id:name_id}]`), and — the identify fix from the
prior discussion — **stamps that stash_id even when the user picks an existing performer** on save,
so future scrapes of the same name auto-resolve regardless of spelling.

---

## 7. Viewer — new "Enrichment" view (Step 1.5, valid names only)

- **Active source** selector (like the native "Active stash-box instance").
- Valid-names list with per-name **Search** → candidate grid → resolve modal (§5.1).
- Batch bar: **Search All** (populate) and **Update** (auto-resolve), with progress + a
  **credit meter** for metered sources.
- Shows which fields are set on each profile and their provenance (`field_sources`).

Built by porting the scene tagger's Tagger result components + `IncludeButton`/`OptionalField`
framework (§1 reuse principle), wired to the §4 service API — not a new tagging UI.

---

## 8. Plugin — image-tagger create upgrade

The per-image slidedown grows from "name only" to the scene-tagger result shape (§1 reuse
principle): the scraped fields shown with the tagger's **✓/✕** `IncludeButton`/`OptionalField`
toggles + an **image carousel**, and **Create / Select / Skip** (`PerformerSelect` used directly).
`PerformerModal`/`StashSearchResult` are internal to Stash, so we **port their structure and reuse
the tagger CSS classes** (not a fresh design), and call `performerCreate` with the composed input.

---

## 9. Credit & rate discipline

Wikidata is free (call liberally). parse.bot is metered: **≤5 req/min**, **199 credits** (test),
gated **behind a Wikidata miss**, **cache-first** (a cached search never re-bills), append-only
ledger + soft ceiling. Batch ops are sequential-async and stop enriching (degrade to name-only)
when the cap is reached — never silently, always surfaced in the view.

---

## 10. Build sequencing

1. **Service engine** — tables, `Provider` seam + Wikidata + parse.bot, cache-first search API,
   profile resolve/apply, batch endpoints + credit ledger, image proxy; wire `enrichment_profile`
   into `/scrape/image` (+ `stored_id` resolution).
2. **Viewer** — the Enrichment view (source selector, candidate grid, resolve modal, batch).
3. **Plugin** — the richer image-tagger create (field ✓/✕, image pick, full `performerCreate`)
   - stamp-on-save for existing performers.
