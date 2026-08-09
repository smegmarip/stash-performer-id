# stash-performer-id

Discovers person names from Stash gallery/folder/file names, lets you triage and assign them,
and registers them as Stash performers with optional external enrichment.

- **Design:** [`docs/DESIGN.md`](docs/DESIGN.md)
- **Plan:** [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md)

## Components

| Path | What | Phase |
|---|---|---|
| `bridge/app/` | Metadata-provider service (name DB, harvest, enrichment; stash-box impersonator) | 1, 4 |
| `plugin/` | Stash UI plugin — Step 2 tagger page (name → performer) | 3 |
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
