# viewer

Standalone React (Vite + TS) app — the **Step 1** UI: name-record management (harvest review,
`valid` triage, edit, direct input; name→asset activation is the next Phase 2 slice). Runs in
the browser and talks to the metadata-provider service's name-DB API (CORS-open).

The API base defaults to `<served-host>:15000`; override with the `VITE_API_BASE` build arg
(Docker) or env var (dev).

## Dev

```bash
npm install
npm run dev        # http://localhost:5173  (service must be on :15000)
npm run build      # typecheck + production bundle → dist/
```

## Container

Built and served by nginx via the root `docker-compose.yml` (viewer on port 15001).
