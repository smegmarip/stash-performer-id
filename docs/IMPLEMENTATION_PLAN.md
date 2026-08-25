# stash-performer-id — Implementation Plan

Phased build of `DESIGN.md`. Each phase is independently testable and leaves the system
working. Target: **Stash v0.30.1 only.**

Topology (DESIGN §10): the **service** owns the name DB, the harvest, and enrichment; the
**viewer** is the Step 1 UI (name-record management); the **Stash plugin** is the Step 2
tagger page only (UI, no Python).

Sequencing principle: **prove harvest/candidate quality in the service before any UI, build
the Step 1 viewer before the Step 2 tagger, and wire the metered enrichment last.**

> **Status (2026-08).** Phases **0–2 are built and committed** (service + name DB + harvest + audit;
> viewer Step-1 activation/cascade + FlyonUI redesign). Deployment is **local Docker**, not the remote
> Unraid host this plan originally named (see the revised Deployment section). **Phase 3 (the image
> tagger) is the current work** — its authoritative spec is **`IMAGE_TAGGER_FEASIBILITY.md`**, which
> supersedes the Phase-3 details below: images-only, plain-JS (no `tsc`), suggestions via the
> provider's `imageByFragment` scraper, and `PerformerSelect` (inline create) — *not* `PerformerModal`.

---

## Phase 0 — Monorepo scaffold
**Goal:** the three pieces exist, deploy, and are reachable.

- [ ] Monorepo skeleton (DESIGN §10): `bridge/app/` (service), `plugin/` (`stash-performer-id.yml`
      `ui:`-only + `js/`+`css/`), `viewer/` (stub), `docker-compose.yml`, `Dockerfile`,
      `pyproject.toml`.
- [ ] Service skeleton: FastAPI + Strawberry (`auto_camel_case=False`), `/graphql` + `/healthz`;
      pydantic-settings with a `SecretStr` parse.bot key + a Stash API key + `topFolder` +
      read-only media mount. Runs on a `15000–15999` port.
- [ ] Plugin: `ui:`-only manifest; `PluginApi.register.route('/plugins/performer-id-tagger',
      Page)` + `patch.before('MainNavBar.UtilityItems', …)` rendering a "hello" page.
- [ ] Bring up the local `docker-compose` stack (service + viewer) against the host's Stash
      (`host.docker.internal:9999`). Smoke test: `/healthz` reachable; nav link opens page.

**Exit:** service + plugin + viewer stub run in the local Docker stack; plugin page routes.

---

## Phase 1 — Service: name DB + harvest + audit (zero Stash writes)
**Goal:** the raw harvest layer populated and inspectable, entirely in the service.

- [ ] Name-DB schema (DESIGN §5): `asset`, `asset_relationship`, `name_candidate`, `names`,
      `name_relationship` (with `UNIQUE(asset_id) WHERE active`); idempotent, indexed.
- [ ] Mechanical gate (DESIGN §4): non-alpha split, whitespace collapse, Unicode-normalize
      (no transliteration), drop <2-char tokens. Unit tests (hashes/junk → nothing; names →
      candidates; multi-source per asset).
- [ ] Stash GraphQL client (API-key header). `harvest_galleries`: `findGalleries` → folder
      path / title / member basenames → `asset`+`asset_relationship`+`name_candidate`.
- [ ] `harvest_path`: crawl the mounted `topFolder`; folder-name-first, fall back to image
      filenames; map paths → Stash entities via the `path` filter.
- [ ] Dedup projection into `names`. `audit` op = harvest + summary (counts by source; sample
      candidates). Batch discipline: progress once, page-1 re-query, resumable checkpoints.
- [ ] Name-DB read API (for the viewer/tagger): list candidates/names/relationships.

**Exit:** an audit run fills the name tables and writes nothing to Stash and calls no
enrichment API. Candidate quality is judgeable (via the API or DB). **First slice ends here.**

---

## Phase 2 — Viewer: Step 1 UI (name-record management)
**Goal:** curate names and activate name→asset relationships.

- [ ] Viewer app (containerized React, own Dockerfile) over the service name-DB API.
- [ ] SCRUD over `names`: browse candidates, dedupe view, `valid` triage, edit
      name/disambiguation, add direct-input names.
- [ ] Activation UI: gallery/folder/file views; activating writes/replaces materialized
      `name_relationship` rows for affected assets (cascade), honoring the active-unique
      constraint; show `source_level` provenance.
- [ ] Name-DB write API endpoints backing the above.

**Exit:** the user curates the name list and activates one name per asset with working
cascade; invalid names are flagged out; direct input works. Still zero Stash writes.

---

## Phase 3 — Stash image tagger: Step 2 (name → performer; Stash writes begin)
**Goal:** turn activated names into Stash performers + associations. **Authoritative spec:
`IMAGE_TAGGER_FEASIBILITY.md`** (images-only; plain JS; provider-scraper suggestions).

- [ ] **Provider scraper surface:** a Stash scraper YAML with `imageByFragment` (`action: script` →
      service HTTP) + a service endpoint that maps an image `path`/`urls` → its activated name in
      `name_relationship`, returning `{performers:[{name, remote_site_id}]}`.
- [ ] **Plugin scaffold:** `ui:`-only manifest (`javascript`/`css`, `requires:
      CommunityScriptsUILibrary`), plain-JS IIFE over `window.PluginApi`, `register.route` + nav patch.
- [ ] **List shell (lift from `tag-manager.js`):** `useFindImagesQuery` + `image_filter`
      (tags/path/organized) + sort + per-page + pager + `useLoadComponents` gate.
- [ ] **Per-image row:** thumbnail, current performers, **Scrape** (`scrapeSingleImage(our-scraper,
      image_id)`) → **`PerformerSelect`** (match existing / inline-create) → **Save**.
- [ ] Save resolves each scraped performer (`stored_id` → `findPerformers(stash_id_endpoint)` →
      `findPerformers(name)` → `performerCreate({name, stash_ids})`) and writes
      `imageUpdate`/`bulkImageUpdate(performer_ids, mode: ADD)`; stamp the name-record `stash_ids`
      on create (DESIGN §8).
- [ ] Idempotency: "already a performer?" via `findPerformers(stash_id_endpoint:{endpoint, …})`.

**Exit:** an activated image name becomes a Stash performer associated with the correct images;
re-runs create no duplicates and merge associations.

---

## Phase 4 — Enrichment: stash-box provider (metered, budget-safe)
**Goal:** native performer enrichment + automatic provider `stash_ids`.

- [ ] Enrichment cache tables in the service SQLite (separate from the name DB).
- [ ] `Provider` protocol + `PerformerData` DTO. `WikidataProvider` (free:
      label/aliases/P569/P106/P18/urls). `TheHandbookProvider` (metered). Merge policy; id =
      QID, fallback `thb:<id>`.
- [ ] Stash-box subset resolvers: `searchPerformer` (id-term short-circuit), `findPerformer`,
      `me`; `/images/<sha>` proxy.
- [ ] Credit guard: thehandbook gated behind Wikidata-miss, token-bucket ≤5 req/min, persisted
      ledger + soft ceiling, cache-first. Verify with ≤5 real credits; confirm cache re-use.
- [ ] Register as a Stash-Box; tag a performer via PerformerTagger; confirm the QID lands in
      `stash_ids` and round-trips on refresh.

**Exit:** PerformerTagger against the service enriches from Wikidata free + thehandbook within
budget, auto-stamps the QID stash_id, and cached names cost zero credits.

---

## Deployment (all phases)
- **Local Docker (revised 2026-08).** The remote Unraid host (`192.168.50.93`) was abandoned; the
  `docker-compose` stack builds and runs on the dev machine against the host's Stash at
  `host.docker.internal:9999`. Local repo is the source of truth.
- Ports single-sourced via env: service `SERVICE_PORT` (15000), viewer `VIEWER_PORT` (15001).

## Cross-cutting / definition of done
- Service + plugin + viewer run in the local Docker stack against Stash v0.30.1.
- The service is the single owner/writer of the name DB.
- No Stash performer write happens except through a user action in the tagger page.
- No credit is spent from harvest/audit; enrichment is cache-first and budget-guarded.
- Association is idempotent: no duplicate performers; associations merge (never clobber).

## Suggested first slice
Phases 0 → 1 (audit). A zero-cost, zero-write harvest over the real library so candidate
quality can be judged before building the viewer, the tagger page, or any enrichment.
