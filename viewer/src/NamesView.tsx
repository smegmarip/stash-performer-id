import { useCallback, useEffect, useState } from "react";

import { api } from "./lib/api";
import type { NameRow, Summary } from "./lib/api";
import { useDebounced } from "./lib/useDebounced";
import { useUrlNumber, useUrlState } from "./lib/useUrlState";
import { Pager } from "./ui/Pager";

const FILTERS = [
  { key: "valid", label: "Valid" },
  { key: "invalid", label: "Invalid" },
  { key: "all", label: "All" },
] as const;

const SORTS = [
  { key: "name", label: "Name" },
  { key: "edited", label: "Edited" },
] as const;

const PAGE = 100;

export default function NamesView() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [names, setNames] = useState<NameRow[]>([]);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useUrlState("status", "valid");
  const [search, setSearch] = useUrlState("q", "");
  const [sort, setSort] = useUrlState("sort", "name");
  const [order, setOrder] = useUrlState("order", "asc");
  const [offset, setOffset] = useUrlNumber("offset", 0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const q = useDebounced(search);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [s, page] = await Promise.all([
        api.summary(),
        api.listNames({
          status: filter === "all" ? undefined : filter,
          q,
          sort,
          order,
          limit: PAGE,
          offset,
        }),
      ]);
      setSummary(s);
      setNames(page.names);
      setTotal(page.total);
      setSelected(new Set());
    } catch (e) {
      setError(String(e));
    }
  }, [filter, q, sort, order, offset]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function withBusy(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
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
  const setValid = (row: NameRow, valid: boolean) =>
    withBusy(() => api.updateName(row.id, { valid }));
  const batchSetValid = (valid: boolean) =>
    withBusy(() => api.setValidBulk([...selected], valid));
  const saveField = (row: NameRow, field: "name" | "disambiguation", value: string) => {
    if (value === (row[field] ?? "")) return;
    void withBusy(() => api.updateName(row.id, { [field]: value }));
  };
  const addName = () => {
    const v = newName.trim();
    if (!v) return;
    setNewName("");
    void withBusy(() => api.addName(v));
  };
  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const allSelected = names.length > 0 && names.every((n) => selected.has(n.id));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-medium">Names</h1>
        <div className="flex gap-2">
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={busy}
            onClick={() =>
              void withBusy(async () => {
                // One button harvests both galleries (→ images) and scenes.
                await api.harvestGalleries();
                await api.harvestScenes();
              })
            }
          >
            <span className="icon-[tabler--download] size-4" />
            Harvest
          </button>
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

      {summary && (
        <div className="stats bg-base-100 shadow-base-300/10 w-full shadow-md">
          <Stat label="Assets" value={summary.assets} icon="icon-[tabler--folder]" />
          <Stat label="Candidates" value={summary.candidates} icon="icon-[tabler--list-search]" />
          <Stat label="Distinct names" value={summary.distinct_names} icon="icon-[tabler--tag]" />
          <Stat label={`${filter} shown`} value={total} icon="icon-[tabler--filter]" />
        </div>
      )}

      {error && (
        <div className="alert alert-error alert-soft" role="alert">
          {error}
        </div>
      )}

      <div className="card shadow-base-300/10 shadow-md">
        <div className="card-header flex flex-wrap items-center gap-3">
          <div className="join">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                className={`join-item btn btn-sm ${filter === f.key ? "btn-primary" : "btn-soft"}`}
                onClick={() => reset(() => setFilter(f.key))}
              >
                {f.label}
              </button>
            ))}
          </div>
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
          <select
            className="select select-sm max-w-36"
            value={sort}
            onChange={(e) => setSort(e.target.value)}
          >
            {SORTS.map((s) => (
              <option key={s.key} value={s.key}>
                Sort: {s.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn btn-soft btn-square btn-sm"
            title="Toggle order"
            onClick={() => setOrder(order === "asc" ? "desc" : "asc")}
          >
            <span className={`size-4 ${order === "asc" ? "icon-[tabler--sort-ascending]" : "icon-[tabler--sort-descending]"}`} />
          </button>

          <div className="ms-auto flex flex-wrap items-center gap-2">
            {selected.size > 0 && (
              <>
                <span className="text-base-content/60 text-sm">{selected.size} selected</span>
                <button
                  type="button"
                  className="btn btn-error btn-soft btn-sm"
                  disabled={busy}
                  onClick={() => void batchSetValid(false)}
                >
                  Invalidate
                </button>
                <button
                  type="button"
                  className="btn btn-success btn-soft btn-sm"
                  disabled={busy}
                  onClick={() => void batchSetValid(true)}
                >
                  Validate
                </button>
                <div className="divider divider-horizontal mx-0" />
              </>
            )}
            <label className="input input-sm max-w-44">
              <span className="icon-[tabler--plus] text-base-content/60 my-auto size-4 shrink-0" />
              <input
                className="grow"
                placeholder="Add name…"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addName()}
              />
            </label>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy || !newName.trim()}
              onClick={addName}
            >
              Add
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
                  <th>Disambiguation</th>
                  <th>Status</th>
                  <th className="text-end">Actions</th>
                </tr>
              </thead>
              <tbody>
                {names.map((row) => (
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
                      <input
                        className="input input-sm w-full"
                        defaultValue={row.name}
                        onBlur={(e) => saveField(row, "name", e.target.value)}
                      />
                    </td>
                    <td>
                      <input
                        className="input input-sm w-full"
                        defaultValue={row.disambiguation}
                        placeholder="—"
                        onBlur={(e) => saveField(row, "disambiguation", e.target.value)}
                      />
                    </td>
                    <td>
                      <span
                        className={`badge badge-soft badge-sm ${row.valid ? "badge-success" : "badge-error"}`}
                      >
                        {row.valid ? "valid" : "invalid"}
                      </span>
                    </td>
                    <td className="text-end">
                      {row.valid ? (
                        <button
                          type="button"
                          className="btn btn-circle btn-text btn-sm"
                          title="invalidate"
                          onClick={() => void setValid(row, false)}
                        >
                          <span className="icon-[tabler--x] size-5" />
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="btn btn-circle btn-text btn-sm"
                          title="validate"
                          onClick={() => void setValid(row, true)}
                        >
                          <span className="icon-[tabler--check] size-5" />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {names.length === 0 && (
                  <tr>
                    <td colSpan={5} className="text-base-content/50 py-8 text-center">
                      No names.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <Pager total={total} offset={offset} page={PAGE} busy={busy} onOffset={setOffset} />
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, icon }: { label: string; value: number; icon: string }) {
  return (
    <div className="stat">
      <div className="stat-figure text-primary">
        <span className={`${icon} size-6`} />
      </div>
      <div className="stat-title">{label}</div>
      <div className="stat-value text-2xl">{value}</div>
    </div>
  );
}
