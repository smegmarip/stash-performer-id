# stash-performer-id — Implementation Plan

Phased build of `DESIGN.md`. Each phase is independently testable and leaves the system
working. Target: **Stash v0.30.1 only.**

Topology (DESIGN §10): the **service** owns the name DB, the harvest, and enrichment; the
**viewer** is the Step 1 UI (name-record management); the **Stash plugin** is the Step 2
tagger page only (UI, no Python).

Sequencing principle: **prove harvest/candidate quality in the service before any UI, build
the Step 1 viewer before the Step 2 tagger, and wire the metered enrichment last.**

---

## Phase 0 — Monorepo scaffold
**Goal:** the three pieces exist, deploy, and are reachable.

- [ ] Monorepo skeleton (DESIGN §10): `bridge/app/` (service), `plugin/` (`celebrity-id.yml`
      `ui:`-only + `js/`+`css/`), `viewer/` (stub), `docker-compose.yml`, `Dockerfile`,
      `pyproject.toml`.
- [ ] Service skeleton: FastAPI + Strawberry (`auto_camel_case=False`), `/graphql` + `/healthz`;
      pydantic-settings with a `SecretStr` parse.bot key + a Stash API key + `topFolder` +
      read-only media mount. Runs on a `15000–15999` port.
- [ ] Plugin: `ui:`-only manifest; `PluginApi.register.route('/plugins/performer-id-tagger',
      Page)` + `patch.before('MainNavBar.UtilityItems', …)` rendering a "hello" page.
- [ ] Deploy to `192.168.50.93:/mnt/user/appdata/stash-performer-id` via rsync + compose
      (confirm remote change first). Smoke test: `/healthz` reachable; nav link opens page.

**Exit:** service + plugin + viewer stub deploy on the remote host; plugin page routes.

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

## Phase 3 — Stash tagger page: Step 2 (name → performer; Stash writes begin)
**Goal:** turn activated names into Stash performers + associations.

- [ ] Tagger page reads each asset's active name from the service API (CORS for the Stash
      origin). Row list with image thumbnail / gallery cover.
- [ ] Reuse Stash core: `PerformerSelect` (match existing), `PerformerModal` (create →
      `performerCreate`), `useFindPerformer`/`usePerformerCreate`.
- [ ] Save: `imageUpdate`/`galleryUpdate`/`sceneUpdate`
      `performer_ids: uniq(existing.concat(chosen))`; bulk `ADD` for one-performer-many-assets.
      Stamp `stash_ids:[{endpoint:"celebrity-id", stash_id:<names.id>}]` (DESIGN §8).
- [ ] Idempotency: resolve "already a performer?" via
      `findPerformers(stash_id_endpoint:{endpoint:"celebrity-id", …})`.

**Exit:** an activated name becomes a Stash performer associated with the correct assets
(per-image where required); re-runs create no duplicates and merge associations.

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
- **Draft locally, build/deploy remotely** — local repo is source of truth; sync via
  rsync/scp; never edit on the remote host.
- Remote host: `192.168.50.93:/mnt/user/appdata/stash-performer-id` (Unraid appdata);
  `docker-compose` on Stash's network; service port from **15000–15999**.
- Any first-time change to the remote host is confirmed with the user before it's made.

## Cross-cutting / definition of done
- Service + plugin + viewer run on the remote host against Stash v0.30.1.
- The service is the single owner/writer of the name DB.
- No Stash performer write happens except through a user action in the tagger page.
- No credit is spent from harvest/audit; enrichment is cache-first and budget-guarded.
- Association is idempotent: no duplicate performers; associations merge (never clobber).

## Suggested first slice
Phases 0 → 1 (audit). A zero-cost, zero-write harvest over the real library so candidate
quality can be judged before building the viewer, the tagger page, or any enrichment.
