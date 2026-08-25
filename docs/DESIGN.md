# stash-performer-id — Design

A Stash plugin that harvests candidate person names from gallery/folder/file names (and
direct input), lets the user triage and assign them to assets, and registers them as Stash
performers — with optional external enrichment. Name *classification* is deliberately not
attempted; resolution is manual and cheap.

Status: **converged design.** Phased build in `IMPLEMENTATION_PLAN.md`.

---

## 1. Target and guiding principles

- **Single target: Stash v0.30.1.** The prod instance will be upgraded to match dev. No
  cross-version branching — we can rely on `urls` (plural), `custom_fields` on performers,
  folder query APIs, and image scraping, all of which exist in 0.30.1.
- **Exclusive, not inclusive.** We do **not** try to decide whether a string "is a name."
  Every candidate that clears a cheap mechanical gate is kept; the human removes the
  invalid ones via a `valid` flag in the UI. (Rationale in §2.)
- **Association is a two-step, separated process** (§3): file→name is plugin-owned data;
  name→performer is a Stash performer registration performed through a plugin-built tagger
  page. The two never conflate.
- **Enrichment is separate, optional, and metered** (§7): a `performerByName` scraper backed
  by Wikidata (free) and thehandbook/parse.bot (200-credit cap). Never on the association
  path.

### Retracted / out of scope
- **Gazetteer / name classifier / probablepeople as a gate.** Dropped. Spot-checks showed
  Wikidata is not a superset of thehandbook (names exist outside both), so any classifier
  would need manual recovery anyway — leaving only the manual triage, which is all we keep.
- **Self-scraping thehandbook.com** (Cloudflare-challenge protected; parse.bot absorbs it).
- ~~**Fragment-scrape bridge for association.**~~ **Reinstated (2026-08).** Step 2 associates
  *natively via the metadata provider*: the image tagger fetches each image's suggested performer
  through the provider's `imageByFragment` **script scraper** (`scrapeSingleImage`), then writes the
  association with a direct `imageUpdate`. The provider is the association vehicle, not just
  enrichment. See **`IMAGE_TAGGER_FEASIBILITY.md`** — the authoritative Step-2 spec, which supersedes
  the §3/§6 "direct mutations, no scraper" / `PerformerModal` details below.
- **Face recognition** (a separate, complementary track).

---

## 2. Why exclusive beats inclusive

Trying to classify a token as a name is unreliable in principle: `probablepeople` passes
non-names (`kitchen table` scored 0.88), and no gazetteer is complete (Wikidata ⊉
thehandbook). Any automated filter therefore has false negatives that must be recovered by
hand — so you build the classifier *and* the manual triage UI. The exclusive strategy keeps
only the triage: harvest everything past a mechanical gate, dedupe, and let the user flip a
`valid` flag. This removes an entire class of hard problems and the credit-waste failure
mode (nothing reaches an API without a human).

---

## 3. The two-step association model (core mental model)

**Step 1 — file → name (UI = the viewer container).** Harvest name candidates from the 4
sources, dedupe into the `names` table, triage `valid`, then *activate* exactly one name per
asset, with a gallery→folder→file cascade. Materialized into the `name_relationship` table.
This is name-record management — it happens in the **standalone viewer** (§10), not in Stash;
nothing touches Stash performers yet.

**Step 2 — name → performer (UI = the Stash image-tagger page).** In the plugin tagger page inside
Stash, each image shows its active name *as a scraped suggestion*, fetched through the provider's
`imageByFragment` script scraper (`scrapeSingleImage(our-scraper, image_id)`). The user confirms or
adjusts with **`PerformerSelect`** (match existing / inline-create), then the page resolves to a
performer id (`stored_id` → `stash_id` → name → `performerCreate`) and writes the association via
`imageUpdate` / `bulkImageUpdate` `performer_ids` (merge-add). **Scope: images** — scenes use Stash's
native tagger, galleries are TBD. Full spec: **`IMAGE_TAGGER_FEASIBILITY.md`** (which supersedes the
`PerformerModal` / "no scraper" mentions in §6 and §8 below).

Key platform facts this respects (verified in source):
- **Gallery performers do not propagate to images.** `GalleryUpdateInput.performer_ids` and
  `ImageUpdateInput.performer_ids` are independent — so "all files under a gallery take the
  name" must expand to a per-image write. Step 1's materialization already produces exactly
  that per-asset row set.
- **There is no image/gallery Tagger in Stash** (only scenes have one, as a `DisplayMode`,
  not a route). Hence the plugin builds its own page (§6).

---

## 4. Name sources and the mechanical gate

**Four candidate sources per asset:** gallery title, folder name, file name, direct input.
Each Stash resource can therefore be identified from up to four origins.

**Mechanical gate (the only automated filtering):**
1. Split on any non-alphabetic run.
2. Collapse repeated whitespace.
3. Unicode-normalize; **do not transliterate** (preserve non-Latin scripts).
4. Drop tokens shorter than 2 characters.

This alone removes hashes (`e8a8d6b7…` → sub-2-char fragments → gone) and most codec/
resolution junk, at negligible cost. Everything surviving is a candidate; validity is a
human decision.

---

## 5. Data architecture (SQLite)

Five tables, **owned by the metadata-provider service** (in the appdata volume, alongside its
enrichment cache). The service runs the harvest that populates them and exposes them through
its API; the viewer does SCRUD via that API; the Stash tagger page reads active names via
that API. Single owner, single writer (§9).

`asset`/`asset_relationship`/`name_candidate` are the raw harvest; `names`/
`name_relationship` are the deduped, triaged, activated layer. There is intentionally **no
performer table** (§8) and **no enrichment tables** — enrichment lives entirely in the
metadata-provider service and is read back through Stash via `stash_ids`/`findPerformer`
(§7). (Table numbers 1-3, 6-7 are kept from the original 8-table proposal for continuity.)

```
── Harvest (raw, non-deduplicated) ────────────────────────────────────────────
1. asset               id PK, resource_type∈{file,folder,gallery}, stash_id,
                       stash_entity_type∈{gallery,scene,image}, path, basename,
                       evaluated_at
2. asset_relationship  id PK, parent_asset_id FK, child_asset_id FK, kind
                       (file→folder, file→gallery); index both directions
3. name_candidate      id PK, asset_id FK, name, original_name, disambiguation,
                       source∈{gallery,folder,file,direct}, evaluation_time
                       -- non-deduplicated, indexed on (name), (asset_id)

── Triaged / activated (deduplicated, UI-editable) ────────────────────────────
6. names               id PK, name UNIQUE, disambiguation, valid BOOL,
                       edited_by?, edited_at?     -- the triage table
7. name_relationship   id PK, name_id FK, asset_id FK, active BOOL,
                       source_level∈{gallery,folder,file,direct}, origin_asset_id,
                       created_at
                       -- UNIQUE(asset_id) WHERE active  (partial index)
                       -- materialized per-asset (incl. cascade descendants)

-- Enrichment is NOT stored here. The metadata-provider service (§7) owns its own
-- SQLite cache of provider results; the performer carries the durable link via stash_ids.
```

Notes:
- **Materialization is the chosen cascade strategy** (confirmed): storage is trivial
  (~1M image rows ≈ tens of MB; SQLite is fine). The only real cost is write-amplification
  on re-activation (a bulk delete+insert in one transaction — sub-second), which is an
  occasional deliberate UI action. Per-asset rows are inherent anyway because gallery
  performers don't propagate to images (§3).
- `source_level` + `origin_asset_id` on the relationship row record *why* an asset has its
  name (gallery vs folder vs file vs direct) so the UI can show provenance and recompute
  cascades; `UNIQUE(asset_id) WHERE active` enforces "one active name per asset" in the DB
  while allowing superseded/inactive history.
- `name_candidate` (3) is non-dedup and indexable — it is the audit trail; `names` (6) is
  its deduplicated projection with the `valid` triage flag.

---

## 6. The plugin Tagger page (the missing batch UI)

> **Superseded by `IMAGE_TAGGER_FEASIBILITY.md` for Step-2 specifics.** That report is the
> authoritative build spec: the tagger is **images-only**, authored as **plain JS** (no `tsc`/build
> step), lifts its filter/sort/pager shell near-verbatim from **`stash-auto-vision-tagging/js/tag-manager.js`**,
> reuses **`PerformerSelect`** (inline create — *not* `PerformerModal`), and fetches suggestions via the
> provider's `imageByFragment` scraper. Read the paragraphs below as the original rationale, not the
> current mechanism.

Stash has no image/gallery tagger, so the plugin ships one as a standalone React page,
adapting the scene Tagger's UX. **Feasibility verified** against the Stash UI plugin API and
the `stash-duplicate-scene-finder` reference.

**Mounting (both the pattern and the reference are confirmed):**
- `PluginApi.register.route('/plugins/stash-performer-id-tagger', TaggerPage)` — standalone page.
- `PluginApi.patch.before('MainNavBar.UtilityItems', …)` — nav-bar entry link.
- Everything from `window.PluginApi`: `React` (+ `React.createElement`, no JSX), `GQL`
  reactive hooks, `libraries.Bootstrap`, `libraries.ReactRouterDOM`, `components.*`; plus
  `csLib.callGQL` for one-shot calls. `requires: CommunityScriptsUILibrary` in the manifest.

**Reused from Stash core (entity-agnostic — confirmed):**
- `PerformerModal` — scraped-performer-shaped input → `PerformerCreateInput`. Feed it a
  synthesized `{ name }`; it handles the create form. (Enrichment fields, when present,
  can prefill it.)
- `PerformerSelect` — typeahead to match an existing performer (with under-18 age guard).
- `usePerformerCreate` / `useFindPerformer` / `findPerformers` hooks.
- `IncludeButton`/`OptionalField`, `LinkButton`, `Shared/Modal` — generic UI.

**Scope: Step 2 only.** The tagger page does name→performer association. Name-record
management — triage/`valid`, name→asset activation/cascade, direct input — lives in the
**viewer** (§10), not here. The page reads each asset's *already-activated* name (from the
service API) and drives the performer side.

**Built new (adapted or replaced):**
- Page shell + row list of assets with their active name, read from the service API.
- Row card: image thumbnail / gallery cover (replacing the scene sprite/vtt card).
- Save: emit `ImageUpdateInput`/`GalleryUpdateInput` with
  `performer_ids: uniq(existing.concat(chosen))` (mirroring the scene tagger's merge) →
  `imageUpdate`/`galleryUpdate`. Use bulk `ADD` mode for many-assets-one-performer.
- Filter the queue by association level (gallery / folder / file) and confirmation status.

**Config persistence:** the tagger config blob can be stored via `configureUI` under the
plugin's own key (same mechanism the scene tagger uses), no dedicated table needed.

**Cascade activation in the UI:** activating a gallery name applies to all descendant files;
switching to folder view and activating overrides them at folder level; file view sets a
single asset. Each activation writes/replaces the materialized `name_relationship` rows for
the affected assets, respecting the partial-unique-active constraint.

---

## 7. Enrichment — a stash-box metadata-provider service

> **Superseded by `ENRICHMENT.md` (2026-08).** The current design makes enrichment a
> **viewer-driven, persisted layer** (asset → name → enriched profile; cache + resolved-profile
> tables FK'd to `names`) that the **image scraper reads** — not primarily a stash-box surface
> consumed by Stash's native PerformerTagger. The `Provider`/`PerformerData` seam, Wikidata +
> parse.bot sources, credit discipline, and QID/`thb:` ids below still hold; read `ENRICHMENT.md`
> for the authoritative model, tables, API, and flows. The paragraphs below are the original framing.

Enrichment is delivered by a **companion service that impersonates a Stash-Box** (Stash's
native metadata-backend protocol), modeled on `iafd-metadata-provider`. It is registered in
Stash under Settings → Metadata Providers → Stash-Boxes with endpoint `${BASE_URL}/graphql`.
This is materially better than a script scraper because Stash gives, natively:
- **PerformerTagger UI integration** — search a name, review candidates, create/update the
  performer through Stash's own flow.
- **Automatic `stash_ids` stamping** — in the stash-box protocol the returned entity `id`
  *is* the `stash_id` Stash stores, so tagging a performer records `{ endpoint: <service-url>,
  stash_id: <id> }` with zero extra work, round-tripping via `findPerformer(id)`. This is the
  persistent name↔record association, delivered natively (see §8).
- **Server-side caching + image proxy** — one SQLite TTL cache; images fetched once, served
  locally.

**Architecture** (FastAPI + Strawberry GraphQL, `StrawberryConfig(auto_camel_case=False)` so
field names match the stash-box SDL):
- **Provider seam:** an internal `Provider` protocol returning a source-neutral
  `PerformerData` DTO; resolvers depend only on the DTO. Two implementations:
  - `WikidataProvider` (free; use liberally) — label/aliases/`P569` birthdate/`P106`
    occupation/`P18` image/urls.
  - `TheHandbookProvider` (metered: **200 credits, 5 req/min**) — socials, thumbnail, tags.
  A merge policy prefers Wikidata for bio and thehandbook for socials.
- **Entity id = stash_id:** the **Wikidata QID** when resolvable (stable, provider-neutral,
  round-trippable); namespaced fallback `thb:<profile_id>` when only thehandbook matches.
  Implement `searchPerformer(term)` (short-circuiting id-shaped terms), `findPerformer(id)`,
  and `me` — the minimum stash-box PerformerTagger subset.
- **Credit discipline centralized here:** thehandbook calls gated behind a Wikidata miss,
  token-bucket ≤5 req/min, persisted credit ledger + soft ceiling, cache-first. The whole
  200-credit budget lives in one place instead of scattered across plugin tasks.
- **Config:** pydantic-settings; add a `SecretStr` field for the parse.bot key. Deployed as
  a Docker sidecar on Stash's network (IAFD-style: `me`/health endpoint, `/images/<sha>`
  proxy, named cache volume).

**Endpoint ownership:** the service owns the **provider** stash_id (one endpoint = the
service URL; the id is the QID or `thb:` fallback). The plugin's tagger page owns a
*separate* **name-record** stash_id (`{ endpoint:"stash-performer-id", stash_id:<names.id> }`, §8).
A fully-processed performer therefore carries up to two stash_ids. A performer created via
the plugin page can be enriched later through the PerformerTagger and vice-versa; idempotency
is by `stash_id` lookup.

**Why stash-box, not a scraper-bridge (confirmed):** the alternative packaging shown by
`stash-extract-db`/`stash-web-scraper` is a thin scraper YAML → HTTP bridge. But `stash_ids`
is stamped only when a stash-box **endpoint** is present (`PerformerModal.tsx:269`:
`if (remoteSiteID && endpoint)`) — a plain scraper has no endpoint and would not persist the
link. So we take the **packaging** from those repos (monorepo: `bridge/app` service +
`docker-compose` + Stash-side adapter + `viewer`) but the **integration protocol** from
`iafd-metadata-provider` (stash-box impersonation), which is what auto-stamps `stash_ids` and
lights up the native PerformerTagger. The companion Docker service is a confirmed commitment.

---

## 8. Performer registration — no plugin performer table

Per the design intent, there is **no plugin-side performer table.** Name→performer identity
lives in Stash via **`stash_ids`**. At create time the tagger page stamps
`stash_ids: [{ endpoint: "stash-performer-id", stash_id: <names.id> }]` onto the performer — the
durable link to the name record for all future actions (re-association, dedup, enrichment,
sync). Enrichment through the metadata-provider service appends the service's own stash_id
(one endpoint = the service URL; id = QID or `thb:` fallback), stamped natively by the
PerformerTagger (§7). So a fully-processed performer carries two stash_ids: the plugin's
name-record link and the service's provider link.

`stash_ids` is settable directly in `performerCreate`/`performerUpdate` (verified: no
stash-box registration required, arbitrary endpoint strings stored verbatim) and is natively
queryable. To answer "is this name already a performer here?", filter
`findPerformers(stash_id_endpoint: {endpoint:"stash-performer-id", stash_id:<names.id>})` — no
mirrored state to keep in sync. The same `stash_id_endpoint` filter exists on image/gallery/
scene lists, so associated assets are discoverable the same way.

---

## 9. Harvest (in the service)

Harvest runs **inside the metadata-provider service** (not a Stash plugin task), triggered
from the viewer. The service reaches Stash with a configured API key and mounts the media
read-only. Three operations, exposed on the service API:

- **`harvest_galleries`** — call Stash GraphQL `findGalleries` (`Gallery.folder.path`,
  `title`, member image paths) → `asset`/`asset_relationship`/`name_candidate` rows → dedupe
  into `names`.
- **`harvest_path`** — filesystem crawl rooted at a `topFolder` config value: test each folder
  name; if none found, descend and fall back to image filenames; map paths back to Stash
  entities via Stash's `path` filter (for later association).
- **`audit`** — harvest only; zero Stash writes, zero enrichment-API calls. The safe first run
  to judge candidate quality.

Batch discipline (lessons from the compreface plugin's real bugs): compute the progress
denominator once; if a Stash query filter self-consumes, re-query **page 1** rather than
incrementing; checkpoint to SQLite as you go; make runs resumable.

Access conventions: the service holds a **Stash API key** (env var; `ApiKey` header on
GraphQL calls) and a **read-only media mount**; always use explicit GraphQL fragments. The
Stash-side plugin no longer runs Python — it is UI-only (§10).

---

## 10. Repository layout & artifacts

Monorepo, following the `stash-extract-db`/`stash-web-scraper` structure:

```
stash-performer-id/
  docker-compose.yml            # orchestrates service + viewer on Stash's network
  Dockerfile                    # metadata-provider service image
  pyproject.toml
  bridge/app/                   # the metadata-provider service (§7, §9) — owns the name DB
    main.py                     #   FastAPI + Strawberry, stash-box GraphQL at /graphql
    api/                        #   stash-box resolvers + name-DB API (for viewer & tagger)
    harvest/                    #   gallery + path harvest (Stash GraphQL client, media mount)
    providers/                  #   Provider protocol + WikidataProvider, TheHandbookProvider
    cache/                      #   SQLite: name tables (§5) + enrichment cache + credit ledger
    stash/                      #   stash-box schema types + PerformerData DTO
  plugin/                       # Stash-side plugin — UI ONLY (installed into Stash plugins/)
    stash-performer-id.yml            #   ui: block only (no exec, no tasks)
    js/  css/                   #   the Step 2 Tagger page (PluginApi)
  viewer/                       # standalone containerized React app (own Dockerfile) — Step 1 UI
  tests/  docs/  scripts/
```

Three cooperating pieces:
- **Metadata-provider service** (`bridge/app`, Docker) — stash-box impersonator wrapping
  Wikidata + thehandbook, **plus** the name DB and the harvest (§9). Registered as a Stash-Box
  endpoint; also exposes a name-DB API for the viewer and the tagger page.
- **Stash UI plugin** (`plugin/`, `ui:`-only, no Python) — the **Step 2** tagger page
  (name→performer). Reads active names from the service API; reuses Stash's
  `PerformerModal`/`PerformerSelect`; writes via `imageUpdate`/`galleryUpdate` + `stash_ids`.
- **Viewer** (`viewer/`, Docker) — the **Step 1** UI: name-record management (harvest review,
  dedupe, `valid` triage, name→asset activation/cascade, direct input) over the service API.

---

## 11. Deployment

**Local Docker (revised 2026-08).** The earlier remote-Unraid plan (`192.168.50.93`) was abandoned —
everything runs in the **local** `docker-compose` stack against the host's Stash.

- **Draft locally, run locally.** The local repo is the source of truth. The compose stack builds and
  runs on the dev machine; the service reaches the host's Stash at `host.docker.internal:9999`.
- **Ports (single-sourced via env, not hardcoded):** service `SERVICE_PORT` (default `15000`), viewer
  `VIEWER_PORT` (default `15001`), from the **15000–15999** range.
- **Registration:** add the service as a Stash-Box (Settings → Metadata Providers → Stash-Boxes),
  endpoint `http://<host>:<port>/graphql`, any non-empty API key. The **image scraper** installs
  separately as a scraper YAML in Stash's `scrapers/` dir (see `IMAGE_TAGGER_FEASIBILITY.md` §2).

---

## 12. Deferred / open
- **Service ↔ tagger-page CORS.** The tagger page (browser, Stash origin) calls the service
  API on a different origin/port — the service must send permissive CORS for the Stash origin.
- **Name-DB API shape** (REST vs a small GraphQL) for the viewer + tagger — settle at build.
- Direct-input UX details (bulk paste, per-asset entry).
- Whether to also expose gallery/scene rows in the same tagger page or separate tabs.
- Enrichment field-mapping specifics (which Wikidata/thehandbook fields populate which
  performer fields) — settle during the service build (Phase 4).
- Name→QID disambiguation is a display aid in the PerformerTagger, deferred to the UI.
