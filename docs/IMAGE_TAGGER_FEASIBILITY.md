# Image Tagger — Feasibility & Scope

Research report for Step 2's core work: a **custom image tagger** as a Stash UI plugin that
scrapes and associates images with performers via our metadata provider. Stash has a native
scene tagger but **no image tagger**; this fills that gap, scoped tightly to the Stash↔provider
contract (no multi-source selection, no tagger config — those are out of scope).

Sources: Stash `pluginApi.tsx`/`patch.tsx`, `components/Tagger/scenes/*`, the GraphQL schema, the
`stash-duplicate-scene-finder` plugin, `pkg/plugin/examples/react-component`, and — the closest
working template — **`stash-auto-vision-tagging/js/tag-manager.js`**, a complete custom plugin *page*
(filtered scene list + `TagSelect` + bulk mutation) authored as plain JS over `window.PluginApi`.
Target: **v0.30.1**.

---

## Verdict

**Feasible, small–medium scope** (revised down after finding `tag-manager.js`). The public plugin API
exposes enough that we **reuse the hard parts** (the real performer picker *with inline create*, the
full GraphQL client, route+nav mounting) **and lift the entire list shell** (filter modal, sort,
per-page, pagination) near-verbatim from `tag-manager.js`, leaving **one genuinely new piece: the
per-image Scrape→PerformerSelect→Save row**. It is **plain JS, no build step**. One architectural
fact shapes the whole design:

> **Image scraping must go through a *script* scraper, not a stash-box.** Stash's stash-box client
> returns `ErrNotSupported` for image-by-fragment (`pkg/scraper/stash.go:434`). So the provider is
> **two surfaces**: a **scraper** (`imageByFragment`) for image→performer association, and the
> **stash-box** (`searchPerformer`/`findPerformer`) for *performer enrichment* (the separate Phase-4
> concern). The image tagger only touches the scraper surface.

Estimated effort: ~1–2 focused sessions for an MVP, most of it the tagger UI + the provider's
image-scraper endpoint. No blockers found.

---

## 1. Exposure gap — what's free vs. what we rebuild

The scene tagger is built entirely from **internal** modules; none of its orchestration is exported.
But its *leaf building blocks* are reachable, and crucially the entire generated GraphQL client and
`StashService` are exposed as whole modules (`PluginApi.GQL = import * as GQL`,
`PluginApi.utils.StashService = import * as StashService`), so runtime reach is **not** limited by
the incomplete `pluginApi.d.ts` type stub.

| Capability | Exposure | How / gap |
|---|---|---|
| **Performer match-existing + create-new** | ✅ **EXPOSED** | `PluginApi.loadableComponents.PerformerSelect` (load via `hooks.useLoadComponents([...])`), then `PluginApi.components.PerformerSelect`. It has **built-in inline create** (`creatable` → `usePerformerCreate`). This is the big win — the scene tagger's whole `PerformerModal`/`PerformerResult` glue is unnecessary for the simple case. |
| **GraphQL (list/update/create/find)** | ✅ EXPOSED | `PluginApi.GQL.{useFindImagesQuery, useImageUpdateMutation, useBulkImageUpdateMutation, usePerformerCreateMutation, useFindPerformersQuery}` — all confirmed generated. |
| **Image scrape** (`ScrapeSingleImage`) | ⚠️ PARTIAL | Absent from the `.d.ts` stub; reach it via `PluginApi.utils.StashService.queryScrapeImage(scraperId, imageId)` / `queryScrapeImageURL(url)`, or a `csLib.callGQL` string. |
| **StashService helpers** | ✅ EXPOSED | `.utils.StashService.{useFindImages, queryFindImages, useImageUpdate, useBulkImageUpdate, usePerformerCreate, queryScrapeImage}`. |
| **Route + nav mount** | ✅ EXPOSED | `PluginApi.register.route("/plugins/…", Page)` + `patch.before("MainNavBar.UtilityItems", …)` — the duplicate-finder pattern. |
| **UI kit** | ✅ EXPOSED | `PluginApi.libraries.{Bootstrap, ReactRouterDOM, ReactSelect, Intl, FontAwesomeSolid}` + `components.{Icon, LoadingIndicator, TruncatedText, HoverPopover}`; `hooks.useToast()`. |
| **Filtered image list widget** | ~ present | `components.{FilteredImageList, ImageCard}` exist, but render the *standard grid*, not tagger rows. We build the list from `useFindImagesQuery` for per-image assignment rows (may reuse `ImageCard` for the thumbnail). |
| **List shell: filter modal + sort + per-page + pagination** | ✅ **LIFTABLE** | `tag-manager.js` already implements the full shell for scenes — `EditFilterModal` (Tags include/exclude + depth, Path via `FolderSelect`/regex, Organized tri-state), `SortControl`, `PerPageSelect`, `PaginationNav`, `useLocalStorage` view state, `useLoadComponents` gate. Lift near-verbatim and swap `findScenes`→`findImages`. |
| **Result-row / modal orchestration** | ❌ REBUILD | The scene tagger's `StashSearchResult`, `PerformerResult`, `sceneTaggerModals`, `context.tsx` are internal-only. We hand-roll the per-image **Scrape → PerformerSelect → Save** row (all primitives reachable). This is the only genuinely net-new UI. |
| **Tag/path/organized filter controls** | ✅ CONFIRMED | `api.components.TagSelect` + `api.components.FolderSelect` used directly in `tag-manager.js`; no force-load, no ReactSelect fallback. |

**Net:** reuse `PerformerSelect` (picker + create), all GraphQL, route/nav, and the UI kit; **lift the
whole list shell (filter modal / sort / per-page / pagination) from `tag-manager.js`**; the only
hand-rolled piece is the per-image tagger row (Scrape/PerformerSelect/Save) and (optionally) a review modal.

---

## 2. The Stash ↔ provider contract for images

- **Invoke:** `scrapeSingleImage(source: {scraper_id: "<our-scraper>"}, input: {image_id: <id>}) → [ScrapedImage!]!`.
- **What the provider's `imageByFragment` receives** (built by Stash from the DB): `{id, title,
  urls, date, details, code, photographer, files[].path (+ fingerprints, size)}`. **No** performers/tags.
  → **Key the provider lookup on `files[].path` (or `urls`).**
- **Return:** `ScrapedImage.performers: [{ name, remote_site_id, stored_id? }]`. Populate
  `remote_site_id` with the provider's id; set `stored_id` only if the provider itself resolved a
  local performer.
- **No "apply scrape" mutation exists.** The client resolves each scraped performer → a local id and
  writes it:
  1. `stored_id` present → use it.
  2. else `findPerformers(performer_filter: {stash_id_endpoint: {endpoint, stash_id, modifier: EQUALS}})`
     (most reliable when `remote_site_id` is set).
  3. else `findPerformers(performer_filter: {name: {value, modifier: EQUALS}})`.
  4. else `performerCreate({name, stash_ids: [{endpoint, stash_id: remote_site_id}]})`.
- **Associate:** `imageUpdate({id, performer_ids: [...]})` (SET) or
  `bulkImageUpdate({ids, performer_ids: {ids, mode: ADD}})` (additive across many images).
- **List/browse:** `findImages(image_filter: {tags: HierarchicalMultiCriterionInput, path:
  StringCriterionInput, organized: Boolean, …}, filter: {page, per_page, sort, direction})` →
  `{count, images{ id title organized paths{thumbnail} visual_files{... on ImageFile{path}} performers{id name} tags{id name} }}`.
- **v0.30.1 note:** use `stash_id_endpoint: StashIDCriterionInput` (the plural `stash_ids_endpoint`
  is post-0.30.1).

### Provider-side work this implies

Our service already holds the name DB and reaches Stash. To be an image scraper it must add:
- A **Stash scraper YAML** (installed in Stash's `scrapers/`) with an `imageByFragment` action —
  either `action: script` (thin transport → our service HTTP) or a JSON scraper pointing at a
  service endpoint. (The `stash-web-scraper`/`stash-extract-db` bridge pattern.)
- A **service endpoint** that, given an image's `path`/`urls`, looks up its activated name in the
  `name_relationship` DB and returns `{performers: [{name, remote_site_id}]}`.

This is how **Step 1 (activation in the viewer)** connects to **Step 2 (tagging in Stash)**: the
provider turns an image's activated name into a scraped performer.

---

## 3. Tagger architecture & flow

A single Stash plugin page (`/plugins/performer-id-image-tagger`), mounted via `register.route` +
a `MainNavBar` nav button. Behaves like the scene tagger, images-only:

```
[ filters: tags · path · organized ]   [ sort ▾ ] [ per-page ]        (built from ImageFilterType)
──────────────────────────────────────────────────────────────
 thumb │ image (title/path) │ current performers │ [ Scrape ] │ suggested → PerformerSelect │ [Save]
 ...one row per image (findImages page)...
──────────────────────────────────────────────────────────────
 [ Prev ]  1–40 of N  [ Next ]           [ Scrape all on page ] [ Save all ]
```

Per-row flow (mirrors the scene tagger):
1. **Scrape** the image via `scrapeSingleImage(our-scraper, image_id)` → suggested performer(s) from
   the provider (i.e. its activated name).
2. **Review/adjust** with `PerformerSelect` — accept the suggestion, pick a different existing
   performer, or type a new one (inline create). This is the "existing or new performer" requirement,
   free from `PerformerSelect`.
3. **Save** → resolve to performer id(s) → `imageUpdate`/`bulkImageUpdate(mode: ADD)`, and stamp the
   name-record `stash_ids` on create.

In scope: image-view **filters (tags/path/organized)**, **pagination**, **sorting** — all via the
`findImages` query. Out of scope (explicit): multi-source selection, tagger configuration, and the
rest of the scene tagger's surface.

---

## 4. Build toolchain — plain JS, no build step

The Stash plugin is **not** the Vite/React SPA viewer. And — confirmed by `tag-manager.js` — it needs
**no build step at all**: author it as **plain JS**, an IIFE over `window.PluginApi`, exactly like the
reference.

- **Plain-JS authoring (chosen).** Each file is `(function(){ 'use strict'; var api =
  window.PluginApi; var React = api.React; var el = React.createElement; … })()`. Deps come off
  `api.{React, GQL, components, libraries, hooks, loadableComponents, register, patch}`. No `tsc`, no
  bundler, no `import`/`require` — ship the `.js` as-authored. (A `tsc`/`module:None` step remains a
  *later* option if we want type-checking, but it is not required and the reference does not use it.)
- **Manifest** (`.yml`) is `ui:`-only: `ui.javascript: [js/…]` + `ui.css: [css/…]`,
  `ui.requires: [CommunityScriptsUILibrary]`. No `exec:`/`interface:` (UI-only; no async backend —
  contrast the vision plugin's Go-RPC manifest, which we do **not** need).
- **Typing:** none required in plain-JS mode. (If we later add `tsc`, copy the hand-written
  `IPluginApi` interface from the examples — there is no published `@types`.)
- Installs into Stash's `plugins/` dir; in our repo it lives in the existing `plugin/` (currently the
  ui-only stub) — this fills it in, mirroring `tag-manager.js`'s `js/` + `css/` layout.

Data access (all proven in `tag-manager.js`): `api.GQL.useXQuery`/`useXMutation` hooks for the
reactive list + bulk writes; `api.utils.StashService.query*` or `csLib.callGQL` for the imperative
`scrapeSingleImage` call.

---

## 5. Scope breakdown

**Reuse (no build):** `PerformerSelect` (+ inline create), `useFindImagesQuery`,
`imageUpdate`/`bulkImageUpdate`/`performerCreate`/`findPerformers`, `queryScrapeImage`, route+nav,
`Icon`/`LoadingIndicator`/`HoverPopover`, `useToast`, Bootstrap/ReactSelect.

**Lift from `tag-manager.js` (adapt, don't invent):** `EditFilterModal` (Tags/Path/Organized),
`SortControl`, `PerPageSelect`, `PaginationNav`, `useLocalStorage`, the `useLoadComponents` gate,
`register.route` + `MainNavBar` nav patch, and the `idsToTagValues`/`tagValuesToIds` TagSelect
helpers. Swap `useFindScenesQuery`/`scene_filter`→`useFindImagesQuery`/`image_filter` and
`bulkSceneUpdate`→`bulkImageUpdate`.

**Build (plugin side, genuinely new):**
- Plugin scaffold: `ui:`-only manifest + plain-JS IIFE entry (no build step).
- Per-image tagger row: thumbnail, current performers, Scrape button, `PerformerSelect`, Save —
  replaces the reference's taxonomy color-coding + bulk tag bar.
- Resolve+associate logic (stored_id → stash_id → name → create; `imageUpdate`/`bulkImageUpdate`).
- Optional: a scrape-review modal — only if we want a richer diff UI; inline `PerformerSelect` per
  row should suffice for MVP.

**Build (provider side):**
- Scraper YAML (`imageByFragment`) + a service endpoint returning performers for an image path/urls.

---

## 6. Decisions & remaining checks

**Decided:**
1. **Suggestion source → through Stash (`scrapeSingleImage(our-scraper, image_id)`)**, for now. The
   association write stays a direct `imageUpdate`; the *suggestion* is fetched via the scrape
   contract, so the same scraper also works in Stash's native per-image "Scrape With…". (The
   direct-read shortcut remains a later option if we want to drop the scraper.)
2. **MVP depth → inline** `PerformerSelect` per row (accept / replace / create right in the row).
   No scrape-review modal for the MVP.

**Tag-filter control — resolved with a working example.** `stash-auto-vision-tagging`
(`js/tag-manager.js:323-362`) uses **`api.components.TagSelect` directly** in a plugin route (grab it
inside the component body). TagSelect works in **value-objects, not raw ids**, so pair it with two
tiny helpers:

```js
var TagSelect = api.components.TagSelect;
el(TagSelect, {
  isMulti: true,
  values: idsToTagValues(selectedIds, allTags),          // [{id,name}], not [id]
  onSelect: (items) => onChange(tagValuesToIds(items)),
  excludeIds: otherPickerIds,
});
// idsToTagValues(ids, allTags) / tagValuesToIds(values)  ~4 lines each (tag-manager.js:296-304)
// allTags via GQL.useFindTagsQuery (tag-manager.js:126)
```

Feed the selected ids into `ImageFilterType.tags {value:[ids], modifier: INCLUDES_ALL, depth:-1}`. No
force-loading and no ReactSelect fallback needed. (The duplicate finder only has a *path* filter —
regex + `FolderSelect` — no tag example; `stash-auto-vision-tagging` is the tag reference.)

**Still to confirm (not blocking):**
- **Scenes/galleries:** scenes use Stash's native tagger (no work). Galleries — TBD; the same plugin
  could add a gallery mode later (galleries support `galleryByFragment`).
5. **Batch cost:** per-image `scrapeSingleImage` on a page of ~40 is fine; "scrape all on page" +
   `bulkImageUpdate` keeps writes cheap.

---

## Recommended MVP

1. Provider: add the `imageByFragment` scraper endpoint (path/urls → activated performer).
2. Plugin: scaffold (`ui:`-only manifest, plain-JS IIFE, route+nav) → **lift the list shell** (filter
   modal / sort / per-page / pager) from `tag-manager.js`, swapping `findScenes`→`findImages` → add the
   per-image row **Scrape → `PerformerSelect` (accept/replace/create) → Save** → resolve+associate.
3. Defer the scrape-review modal and gallery mode until the core loop works end-to-end.
