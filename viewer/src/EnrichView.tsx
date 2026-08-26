import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./lib/api";
import type { Candidate, EnrichSource, NameRow } from "./lib/api";
import { useDebounced } from "./lib/useDebounced";
import { useUrlNumber, useUrlState } from "./lib/useUrlState";
import { EnrichModal } from "./ui/EnrichModal";
import { Pager } from "./ui/Pager";

const PAGE = 50;
type Status = Record<number, { fields: number; sources: string[] }>;
// Transient per-row state while a search runs, so each row updates live as its own request
// resolves (the Stash tagger paradigm) — and on completion holds the candidates to render inline.
type RowBatch =
  | { phase: "searching" }
  | { phase: "done"; candidates?: Candidate[]; applied?: number; error?: string | null };

export default function EnrichView() {
  const [sources, setSources] = useState<EnrichSource[]>([]);
  const [source, setSource] = useUrlState("src", "");
  const [names, setNames] = useState<NameRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useUrlNumber("offset", 0);
  const [search, setSearch] = useUrlState("q", "");
  const [status, setStatus] = useState<Status>({});
  const [credits, setCredits] = useState<{ spent: number; budget: number } | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [modal, setModal] = useState<{
    nameId: number;
    name: string;
    candidate?: Candidate;
  } | null>(null);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [rowBatch, setRowBatch] = useState<Record<number, RowBatch>>({});
  const stopRef = useRef(false);
  const q = useDebounced(search);

  const metered = sources.find((s) => s.id === source)?.metered ?? false;

  useEffect(() => {
    let cancelled = false;
    void api.enrichSources().then((r) => {
      if (cancelled) return;
      setSources(r.sources);
      const def = r.sources.find((s) => s.id === "babepedia")?.id || r.sources[0]?.id || "";
      setSource(source || def);
    });
    return () => {
      cancelled = true;
    };
    // Run once on mount to load sources and pick a default.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const page = await api.listNames({ status: "valid", q, limit: PAGE, offset });
      setNames(page.names);
      setTotal(page.total);
      setSelected(new Set());
      const ids = page.names.map((n) => n.id);
      setStatus(ids.length ? (await api.enrichProfileStatus(ids)).profiles : {});
      setCredits(metered ? (await api.enrichCredits())["parsebot"] ?? null : null);
    } catch (e) {
      setError(String(e));
    }
  }, [q, offset, metered]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const targets = () => (selected.size ? [...selected] : names.map((n) => n.id));

  // The search interface for one name: searching -> /enrichment/search (cache-first) -> hold the
  // candidates so the row renders them inline. Used by the per-row button and the batch loop.
  async function searchOne(id: number): Promise<number> {
    setRowBatch((s) => ({ ...s, [id]: { phase: "searching" } }));
    try {
      const r = await api.enrichSearch(id, source);
      setRowBatch((s) => ({
        ...s,
        [id]: { phase: "done", candidates: r.candidates, error: r.error },
      }));
      return r.candidates.length;
    } catch (e) {
      setRowBatch((s) => ({ ...s, [id]: { phase: "done", error: String(e) } }));
      return 0;
    }
  }

  // Drive the batch client-side, one name at a time, so every row reflects its own request as it
  // resolves: searching -> external API -> DB persist (server) -> live UI update for that row.
  // Sequential (Stash Batch Search paradigm) and cancellable via Stop.
  async function runBatch(kind: "search" | "update") {
    if (!source) return;
    const ids = targets();
    if (!ids.length) return;
    setBusy(true);
    setError(null);
    setNote(null);
    setRowBatch({});
    stopRef.current = false;
    setProgress({ done: 0, total: ids.length });

    let done = 0;
    let found = 0;
    for (const id of ids) {
      if (stopRef.current) break;
      try {
        if (kind === "search") {
          if ((await searchOne(id)) > 0) found++;
        } else {
          setRowBatch((s) => ({ ...s, [id]: { phase: "searching" } }));
          const res = (await api.updateBatch([id], source, [])).results[0];
          const applied = res?.applied ?? 0;
          if (applied > 0) found++;
          setRowBatch((s) => ({ ...s, [id]: { phase: "done", applied, error: res?.error ?? null } }));
          // Auto-resolve wrote the profile — pull this row's fresh status so its badge updates now.
          const st = await api.enrichProfileStatus([id]);
          setStatus((prev) => ({ ...prev, ...st.profiles }));
        }
      } catch (e) {
        setRowBatch((s) => ({ ...s, [id]: { phase: "done", error: String(e) } }));
      }
      done += 1;
      setProgress((p) => (p ? { ...p, done } : p));
      if (metered) setCredits((await api.enrichCredits())["parsebot"] ?? null);
    }

    const stopped = stopRef.current;
    const verb = kind === "search" ? "Searched" : "Auto-resolved";
    const tail = kind === "search" ? `${found} with candidates` : `${found} resolved`;
    setNote(`${stopped ? "Stopped — " : ""}${verb} ${done}/${ids.length} on ${source}: ${tail}.`);
    setBusy(false);
    setProgress(null);
  }

  const reset = (fn: () => void) => {
    setOffset(0);
    fn();
  };
  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const allSelected = names.length > 0 && names.every((n) => selected.has(n.id));
  const scope = selected.size || names.length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-medium">Enrichment</h1>
        <div className="flex items-center gap-2">
          {metered && credits && (
            <span className="badge badge-soft badge-warning" title="parse.bot credits">
              {credits.spent}/{credits.budget} credits
            </span>
          )}
          <button type="button" className="btn btn-soft btn-sm" disabled={busy} onClick={() => void refresh()}>
            <span className="icon-[tabler--refresh] size-4" />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="alert alert-error alert-soft" role="alert">
          {error}
        </div>
      )}
      {note && (
        <div className="alert alert-info alert-soft" role="status">
          {note}
        </div>
      )}

      <div className="card shadow-base-300/10 shadow-md">
        <div className="card-header flex flex-wrap items-center gap-3">
          <label className="text-base-content/70 text-sm">Source</label>
          <select
            className="select select-sm max-w-44"
            value={source}
            onChange={(e) => reset(() => setSource(e.target.value))}
          >
            {sources.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
                {s.metered ? " (metered)" : ""}
              </option>
            ))}
          </select>
          <label className="input input-sm max-w-52">
            <span className="icon-[tabler--search] text-base-content/60 my-auto size-4 shrink-0" />
            <input
              type="search"
              className="grow"
              placeholder="Search names…"
              value={search}
              onChange={(e) => reset(() => setSearch(e.target.value))}
            />
          </label>

          <div className="ms-auto flex items-center gap-2">
            {progress ? (
              <>
                <span className="text-base-content/60 text-sm tabular-nums">
                  {progress.done}/{progress.total}
                </span>
                <progress
                  className="progress progress-primary w-24"
                  value={progress.done}
                  max={progress.total}
                />
                <button
                  type="button"
                  className="btn btn-error btn-soft btn-sm"
                  onClick={() => {
                    stopRef.current = true;
                  }}
                  title="Stop after the current name"
                >
                  <span className="icon-[tabler--player-stop] size-4" />
                  Stop
                </button>
              </>
            ) : (
              <>
                <span className="text-base-content/60 text-sm">
                  {selected.size ? `${selected.size} selected` : `${names.length} on page`}
                </span>
                <button
                  type="button"
                  className="btn btn-soft btn-sm"
                  disabled={busy || !scope}
                  onClick={() => void runBatch("search")}
                  title="Populate candidates for these names (no resolution)"
                >
                  <span className="icon-[tabler--search] size-4" />
                  Search all ({scope})
                </button>
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  disabled={busy || !scope}
                  onClick={() => void runBatch("update")}
                  title="Auto-resolve: apply the best match onto each profile"
                >
                  <span className="icon-[tabler--wand] size-4" />
                  Update ({scope})
                </button>
              </>
            )}
          </div>
        </div>

        <div className="card-body p-0">
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th className="w-0">
                    <input
                      type="checkbox"
                      className="checkbox checkbox-sm"
                      checked={allSelected}
                      onChange={() =>
                        setSelected(allSelected ? new Set() : new Set(names.map((n) => n.id)))
                      }
                      aria-label="select all"
                    />
                  </th>
                  <th>Name</th>
                  <th>Enrichment</th>
                  <th className="text-end">Action</th>
                </tr>
              </thead>
              <tbody>
                {names.map((row) => {
                  const st = status[row.id];
                  const rb = rowBatch[row.id];
                  return (
                    <tr key={row.id}>
                      <td>
                        <input
                          type="checkbox"
                          className="checkbox checkbox-sm"
                          checked={selected.has(row.id)}
                          onChange={() => toggle(row.id)}
                          aria-label={`select ${row.name}`}
                        />
                      </td>
                      <td>
                        <div className="font-medium">{row.name}</div>
                        {row.disambiguation && (
                          <div className="text-base-content/40 text-xs">{row.disambiguation}</div>
                        )}
                      </td>
                      <td>
                        {rb?.phase === "searching" ? (
                          <span className="text-base-content/60 inline-flex items-center gap-1.5 text-sm">
                            <span className="loading loading-spinner loading-xs" />
                            Searching…
                          </span>
                        ) : rb?.phase === "done" && rb.error ? (
                          <span className="badge badge-soft badge-error badge-sm" title={rb.error}>
                            error
                          </span>
                        ) : rb?.phase === "done" && rb.candidates && rb.candidates.length ? (
                          // Candidate matches inline, like a Stash tagger card: a grid of results
                          // with a visible thumbnail + name/disambiguation. Click one to resolve.
                          <div className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-1">
                            {rb.candidates.map((c) => {
                              const img = (c.data.images ?? [])[0] as string | undefined;
                              return (
                                <button
                                  key={`${c.source}-${c.source_entity_id}`}
                                  type="button"
                                  className="hover:bg-base-200 flex items-center gap-2 rounded-lg p-1 text-start"
                                  onClick={() =>
                                    setModal({ nameId: row.id, name: row.name, candidate: c })
                                  }
                                  title="Open to resolve"
                                >
                                  {img ? (
                                    <img
                                      src={img}
                                      alt=""
                                      className="size-12 shrink-0 rounded object-cover"
                                      loading="lazy"
                                    />
                                  ) : (
                                    <span className="bg-base-300 flex size-12 shrink-0 items-center justify-center rounded">
                                      <span className="icon-[tabler--user] text-base-content/40 size-6" />
                                    </span>
                                  )}
                                  <span className="min-w-0">
                                    <span className="block truncate text-sm font-medium">
                                      {c.data.name}
                                    </span>
                                    {c.data.disambiguation != null && (
                                      <span className="text-base-content/50 block truncate text-xs">
                                        {String(c.data.disambiguation)}
                                      </span>
                                    )}
                                  </span>
                                </button>
                              );
                            })}
                          </div>
                        ) : rb?.phase === "done" ? (
                          <span className="badge badge-soft badge-sm">no match</span>
                        ) : st ? (
                          <span className="badge badge-soft badge-success badge-sm" title={st.sources.join(", ")}>
                            {st.fields} fields · {st.sources.join(", ")}
                          </span>
                        ) : (
                          <span className="text-base-content/40 text-sm">—</span>
                        )}
                      </td>
                      <td className="text-end">
                        <button
                          type="button"
                          className="btn btn-soft btn-sm"
                          disabled={!source || busy || rb?.phase === "searching"}
                          onClick={() => void searchOne(row.id)}
                          title="Search this name for candidates"
                        >
                          <span className="icon-[tabler--search] size-4" />
                          Search
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {names.length === 0 && (
                  <tr>
                    <td colSpan={4} className="text-base-content/50 py-8 text-center">
                      No valid names.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <Pager total={total} offset={offset} page={PAGE} busy={busy} onOffset={setOffset} />
        </div>
      </div>

      {modal && source && (
        <EnrichModal
          nameId={modal.nameId}
          name={modal.name}
          source={source}
          initialCandidate={modal.candidate}
          onClose={() => setModal(null)}
          onApplied={() => void refresh()}
        />
      )}
    </div>
  );
}
