# stash-performer-id

Discovers person names from Stash gallery/folder/file names, lets you triage and assign them,
and registers them as Stash performers with optional external enrichment.

- **Design:** [`docs/DESIGN.md`](docs/DESIGN.md)
- **Plan:** [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md)

## Components

| Path | What | Phase |
|---|---|---|
| `bridge/app/` | Metadata-provider service (name DB, harvest, enrichment; stash-box impersonator) | 1, 4 |
| `plugin/` | Stash UI plugin — Step 2 image tagger page (name → performer) | 3 |
| `scraper/` | Stash `imageByFragment` scraper — image → activated name, via the service | 3 |
| `viewer/` | Standalone React app — Step 1 UI (name-record management) | 2 |

Target: **Stash v0.30.1**. Service port range **15000–15999**.

## Dev

```bash
uv sync                       # install deps (+ dev group)
uv run pytest                 # run tests
uv run python -m bridge.app   # run the service (PORT, default 15000)
```

Or containerized (service + viewer):

```bash
docker compose up --build     # service on :SERVICE_PORT (15000), viewer on :VIEWER_PORT (15001)
```

Ports are driven by `SERVICE_PORT` / `VIEWER_PORT` (see `.env.example`); everything else
derives from them.

## Step 2 — install the image tagger in Stash

The service runs in Docker; the **plugin** and **scraper** install into your host Stash. After
activating names in the viewer (Step 1):

1. **Scraper** — copy `scraper/` into Stash's scrapers dir as its own folder, e.g.
   `~/.stash/scrapers/stash-performer-id/` (containing `stash-performer-id.yml` +
   `stash-performer-id-scrape.py`). The transport reaches the service at
   `http://localhost:15000` by default — override with the `STASH_PERFORMER_ID_URL` env var if
   `SERVICE_PORT` differs. Needs `python3` on the Stash host/container.
2. **Plugin** — copy `plugin/` into Stash's plugins dir, e.g.
   `~/.stash/plugins/stash-performer-id/`.
3. In Stash → **Settings → Metadata Providers → Scrapers → Reload**, and **Settings → Plugins →
   Reload**. A person-tag icon appears in the nav bar → opens the image tagger.

Per image: **Scrape** (pulls the activated name via the scraper) → pick/confirm or **Create** the
performer → **Save** (`imageUpdate`). Filter by tags/path/organized, sort, and paginate like the
native image grid. See [`docs/IMAGE_TAGGER_FEASIBILITY.md`](docs/IMAGE_TAGGER_FEASIBILITY.md).
