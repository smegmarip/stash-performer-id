import { useCallback, useEffect, useState } from "react";

import { api } from "./lib/api";
import type { EnrichSource, NameRow } from "./lib/api";
import { useDebounced } from "./lib/useDebounced";
import { useUrlNumber, useUrlState } from "./lib/useUrlState";
import { EnrichModal } from "./ui/EnrichModal";
import { Pager } from "./ui/Pager";

const PAGE = 50;
type Status = Record<number, { fields: number; sources: string[] }>;

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
  const [modal, setModal] = useState<{ nameId: number; name: string } | null>(null);
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

  async function runBatch(kind: "search" | "update") {
    if (!source) return;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const ids = targets();
      if (kind === "search") {
        const r = await api.searchBatch(ids, source);
        const found = r.results.filter((x) => (x.count ?? 0) > 0).length;
        setNote(`Searched ${r.results.length} name(s) on ${source}: ${found} with candidates.`);
      } else {
        const r = await api.updateBatch(ids, source, []);
        const applied = r.results.filter((x) => (x.applied ?? 0) > 0).length;
        setNote(`Auto-resolved ${applied}/${r.results.length} name(s) from ${source}.`);
      }
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
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
              {busy ? <span className="loading loading-spinner loading-xs" /> : <span className="icon-[tabler--search] size-4" />}
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
                        {st ? (
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
                          disabled={!source}
                          onClick={() => setModal({ nameId: row.id, name: row.name })}
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
          onClose={() => setModal(null)}
          onApplied={() => void refresh()}
        />
      )}
    </div>
  );
}
