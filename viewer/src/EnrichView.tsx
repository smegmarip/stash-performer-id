import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./lib/api";
import type { Candidate, EnrichSource, NameRow } from "./lib/api";
import { useDebounced } from "./lib/useDebounced";
import { useUrlNumber, useUrlState } from "./lib/useUrlState";
import { EnrichModal } from "./ui/EnrichModal";
import { Pager } from "./ui/Pager";
import { PerPage, usePerPage } from "./ui/PerPage";
import { ProfileModal } from "./ui/ProfileModal";

type Status = Record<
  number,
  { fields: number; sources: string[]; image: string | null }
>;
const FILTERS = [
  { value: "all", label: "All" },
  { value: "matched", label: "Matched" },
  { value: "unmatched", label: "Unmatched" },
];
// A small favicon per provider, overlaid on the profile thumbnail to show which source(s) fed it.
// Mixed-source profiles show one favicon per contributing source, side by side.
const SOURCE_FAVICON: Record<string, string> = {
  babepedia: "https://www.babepedia.com/favicon.ico",
  wikidata: "https://www.wikidata.org/favicon.ico",
  parsebot: "https://parse.bot/favicon.ico",
};
// Transient per-row state while a search runs, so each row updates live as its own request
// resolves (the Stash tagger paradigm) — and on completion holds the candidates to render inline.
type RowBatch =
  | { phase: "searching" }
  | {
      phase: "done";
      candidates?: Candidate[];
      applied?: number;
      error?: string | null;
    };

export default function EnrichView() {
  const [sources, setSources] = useState<EnrichSource[]>([]);
  const [source, setSource] = useUrlState("src", "");
  const [filter, setFilter] = useUrlState("filter", "all");
  const [names, setNames] = useState<NameRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useUrlNumber("offset", 0);
  const [perPage, setPerPage] = usePerPage(50);
  const [search, setSearch] = useUrlState("q", "");
  const [status, setStatus] = useState<Status>({});
  const [credits, setCredits] = useState<{
    spent: number;
    budget: number;
  } | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [modal, setModal] = useState<{
    nameId: number;
    name: string;
    candidate?: Candidate;
  } | null>(null);
  const [progress, setProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);
  const [rowBatch, setRowBatch] = useState<Record<number, RowBatch>>({});
  const [profileModal, setProfileModal] = useState<{
    nameId: number;
    name: string;
  } | null>(null);
  const stopRef = useRef(false);
  const q = useDebounced(search);

  const metered = sources.find((s) => s.id === source)?.metered ?? false;

  useEffect(() => {
    let cancelled = false;
    void api.enrichSources().then((r) => {
      if (cancelled) return;
      setSources(r.sources);
      const def =
        r.sources.find((s) => s.id === "babepedia")?.id ||
        r.sources[0]?.id ||
        "";
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
      const page = await api.listNames({
        status: "valid",
        q,
        limit: perPage,
        offset,
        enriched: filter === "all" ? undefined : filter,
      });
      setNames(page.names);
      setTotal(page.total);
      setSelected(new Set());
      const ids = page.names.map((n) => n.id);
      setStatus(
        ids.length ? (await api.enrichProfileStatus(ids)).profiles : {},
      );
      setCredits(
        metered ? ((await api.enrichCredits())["parsebot"] ?? null) : null,
      );
    } catch (e) {
      setError(String(e));
    }
  }, [q, offset, perPage, metered, filter]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const targets = () =>
    selected.size ? [...selected] : names.map((n) => n.id);

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
          setRowBatch((s) => ({
            ...s,
            [id]: { phase: "done", applied, error: res?.error ?? null },
          }));
          // Auto-resolve wrote the profile — pull this row's fresh status so its badge updates now.
          const st = await api.enrichProfileStatus([id]);
          setStatus((prev) => ({ ...prev, ...st.profiles }));
        }
      } catch (e) {
        setRowBatch((s) => ({
          ...s,
          [id]: { phase: "done", error: String(e) },
        }));
      }
      done += 1;
      setProgress((p) => (p ? { ...p, done } : p));
      if (metered) setCredits((await api.enrichCredits())["parsebot"] ?? null);
    }

    const stopped = stopRef.current;
    const label = kind === "search" ? "with candidates" : "resolved";
    setNote(
      `${source} — ${done}/${ids.length} ${kind === "search" ? "searched" : "processed"}, ` +
        `${found} ${label}${stopped ? " (stopped)" : ""}`,
    );
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
  const allSelected =
    names.length > 0 && names.every((n) => selected.has(n.id));
  const scope = selected.size || names.length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-medium">Enrichment</h1>
        <div className="flex items-center gap-2">
          {metered && credits && (
            <span
              className="badge badge-soft badge-warning"
              title="parse.bot credits"
            >
              {credits.spent}/{credits.budget} credits
            </span>
          )}
          <button
            type="button"
            className="btn btn-soft btn-sm"
            disabled={busy}
            onClick={() => void refresh()}
          >
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
          <div
            className="join"
            role="group"
            aria-label="filter by match status"
          >
            {FILTERS.map((f) => (
              <button
                key={f.value}
                type="button"
                className={`btn btn-sm join-item ${filter === f.value ? "btn-primary" : "btn-soft"}`}
                onClick={() => reset(() => setFilter(f.value))}
              >
                {f.label}
              </button>
            ))}
          </div>
          <PerPage value={perPage} onChange={(n) => reset(() => setPerPage(n))} disabled={busy} />

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
                {selected.size > 0 && (
                  <span className="text-base-content/60 text-sm">
                    {selected.size} selected
                  </span>
                )}
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
          <table className="table">
            <thead>
              <tr>
                <th className="w-0">
                  <input
                    type="checkbox"
                    className="checkbox checkbox-sm"
                    checked={allSelected}
                    onChange={() =>
                      setSelected(
                        allSelected
                          ? new Set()
                          : new Set(names.map((n) => n.id)),
                      )
                    }
                    aria-label="select all"
                  />
                </th>
                <th className="w-0">Image</th>
                <th>Name</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {names.map((row) => {
                const st = status[row.id];
                const rb = rowBatch[row.id];
                // One table row per name: the header and its results share this row, so the only
                // horizontal border is the table's own divider (between names). Results are
                // separated from one another by background color, not borders.
                return (
                  <tr key={row.id}>
                    <td className="align-top">
                      <input
                        type="checkbox"
                        className="checkbox checkbox-sm"
                        checked={selected.has(row.id)}
                        onChange={() => toggle(row.id)}
                        aria-label={`select ${row.name}`}
                      />
                    </td>
                    {/* Enrichment thumbnail — its own column between checkbox and name. */}
                    <td className="align-top">
                      {st && (
                        <button
                          type="button"
                          className="relative block size-12 cursor-pointer"
                          title={`${st.fields} fields · ${st.sources.join(", ")} — click to view profile`}
                          onClick={() =>
                            setProfileModal({ nameId: row.id, name: row.name })
                          }
                        >
                          {st.image ? (
                            <img
                              src={st.image}
                              alt=""
                              className="size-12 rounded object-cover"
                              loading="lazy"
                            />
                          ) : (
                            <div className="bg-base-300 flex size-12 items-center justify-center rounded">
                              <span className="icon-[tabler--user] text-base-content/30 size-6" />
                            </div>
                          )}
                          <div className="absolute -left-1 -top-1 flex gap-0.5">
                            {st.sources.map((s) =>
                              SOURCE_FAVICON[s] ? (
                                <img
                                  key={s}
                                  src={SOURCE_FAVICON[s]}
                                  alt={s}
                                  title={s}
                                  className="bg-base-100 size-3.5 rounded-full ring-1 ring-black/20"
                                  onError={(e) =>
                                    (e.currentTarget.style.display = "none")
                                  }
                                />
                              ) : null,
                            )}
                          </div>
                          <span className="absolute bottom-0 right-0 rounded-tl rounded-br bg-black/70 px-1 text-[10px] font-medium leading-4 text-white">
                            {st.fields}
                          </span>
                        </button>
                      )}
                    </td>
                    <td>
                      <div className="font-medium">{row.name}</div>
                      {row.disambiguation && (
                        <div className="text-base-content/40 text-xs">
                          {row.disambiguation}
                        </div>
                      )}

                      {rb && (
                        <div className="mt-2 flex flex-col gap-1">
                          {rb.phase === "searching" ? (
                            <span className="text-base-content/60 inline-flex items-center gap-1.5 text-sm">
                              <span className="loading loading-spinner loading-xs" />
                              Searching {source}…
                            </span>
                          ) : rb.error ? (
                            <span
                              className="badge badge-soft badge-error badge-sm w-fit"
                              title={rb.error}
                            >
                              {rb.error}
                            </span>
                          ) : rb.candidates && rb.candidates.length ? (
                            rb.candidates.map((c) => {
                              const img = (c.data.images ?? [])[0] as
                                | string
                                | undefined;
                              return (
                                <button
                                  key={`${c.source}-${c.source_entity_id}`}
                                  type="button"
                                  className="bg-base-200/60 hover:bg-base-300/60 flex w-full items-center gap-3 rounded-md px-2 py-1.5 text-start"
                                  onClick={() =>
                                    setModal({
                                      nameId: row.id,
                                      name: row.name,
                                      candidate: c,
                                    })
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
                                  <span className="min-w-0 flex-1">
                                    <span className="block truncate text-sm font-medium">
                                      {c.data.name}
                                    </span>
                                    {c.data.disambiguation != null && (
                                      <span className="text-base-content/50 block truncate text-xs">
                                        {String(c.data.disambiguation)}
                                      </span>
                                    )}
                                  </span>
                                  <span className="icon-[tabler--chevron-right] text-base-content/30 size-4 shrink-0" />
                                </button>
                              );
                            })
                          ) : (
                            <span className="text-base-content/50 text-sm">
                              No candidates on {source}.
                            </span>
                          )}
                        </div>
                      )}
                    </td>
                    <td className="align-top text-end">
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
                  <td
                    colSpan={4}
                    className="text-base-content/50 py-8 text-center"
                  >
                    No valid names.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          <Pager
            total={total}
            offset={offset}
            page={perPage}
            busy={busy}
            onOffset={setOffset}
            alwaysShow
          />
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

      {profileModal && (
        <ProfileModal
          nameId={profileModal.nameId}
          name={profileModal.name}
          onClose={() => setProfileModal(null)}
        />
      )}
    </div>
  );
}
