import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./lib/api";
import type { AssetRow, NameRow, Scope } from "./lib/api";
import { useDebounced } from "./lib/useDebounced";
import { useUrlNumber, useUrlState } from "./lib/useUrlState";
import { AssignCombobox } from "./ui/AssignCombobox";
import { Pager } from "./ui/Pager";
import { PerPage, usePerPage } from "./ui/PerPage";

const SCOPES: { key: Scope; label: string; icon: string }[] = [
  { key: "gallery", label: "Gallery", icon: "icon-[tabler--photo]" },
  { key: "folder", label: "Folder", icon: "icon-[tabler--folder]" },
  { key: "file", label: "File", icon: "icon-[tabler--file]" },
];
const ASSIGNED = [
  { key: "all", label: "All" },
  { key: "assigned", label: "Assigned" },
  { key: "unassigned", label: "Unassigned" },
  { key: "ignored", label: "Ignored" },
] as const;
// File assets are either images or scenes; this sub-filter is only shown for the File scope.
const ENTITY_TYPES = [
  { key: "image", label: "Image", icon: "icon-[tabler--photo]" },
  { key: "scene", label: "Scene", icon: "icon-[tabler--movie]" },
] as const;
const SORTS = [
  { key: "path", label: "Path" },
  { key: "name", label: "Name" },
] as const;

export default function AssetsView() {
  const [scopeStr, setScope] = useUrlState("scope", "gallery");
  const scope = scopeStr as Scope;
  const [assets, setAssets] = useState<AssetRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useUrlNumber("offset", 0);
  const [perPage, setPerPage] = usePerPage(100);
  const [search, setSearch] = useUrlState("q", "");
  const [sort, setSort] = useUrlState("sort", "path");
  const [order, setOrder] = useUrlState("order", "asc");
  const [assigned, setAssigned] = useUrlState("assigned", "all");
  const [entity, setEntity] = useUrlState("entity", "image");
  const [validNames, setValidNames] = useState<NameRow[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const q = useDebounced(search);
  const selectAllRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [page, names] = await Promise.all([
        api.listAssets({
          type: scope,
          q,
          sort,
          order,
          assigned: assigned === "all" ? undefined : assigned,
          entity_type: scope === "file" ? entity : undefined,
          limit: perPage,
          offset,
        }),
        api.listNames({ status: "valid", limit: 1000 }),
      ]);
      setAssets(page.assets);
      setTotal(page.total);
      setValidNames(names.names);
      setSelected(new Set()); // selection is per-view; drop it whenever the list reloads
    } catch (e) {
      setError(String(e));
    }
  }, [scope, q, sort, order, assigned, entity, perPage, offset]);

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
  const assignedCount = assets.filter((a) => a.active).length;

  // --- batch selection ---
  const allChecked = assets.length > 0 && assets.every((a) => selected.has(a.asset_id));
  const someChecked = assets.some((a) => selected.has(a.asset_id));
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = someChecked && !allChecked;
  }, [someChecked, allChecked]);

  const toggleAll = () =>
    setSelected(allChecked ? new Set() : new Set(assets.map((a) => a.asset_id)));
  const toggleOne = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // Apply one action to every selected asset (sequentially — the DB is a single shared connection).
  const batchAssign = (nameId: number) => {
    const ids = [...selected];
    if (!ids.length) return;
    void withBusy(async () => {
      for (const id of ids) await api.activate(id, nameId, scope);
    });
  };
  const batchClear = () => {
    const ids = [...selected];
    if (!ids.length) return;
    void withBusy(async () => {
      for (const id of ids) await api.deactivate(id);
    });
  };
  const batchIgnore = () => {
    const ids = [...selected];
    if (!ids.length) return;
    void withBusy(() => api.ignoreBulk(ids, true));
  };
  const batchRestore = () => {
    const ids = [...selected];
    if (!ids.length) return;
    void withBusy(() => api.ignoreBulk(ids, false));
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-medium">Assets</h1>
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

      <div className="stats bg-base-100 shadow-base-300/10 w-full shadow-md">
        <Stat label={`${scope} total`} value={total} icon="icon-[tabler--stack-2]" />
        <Stat label="assigned (page)" value={assignedCount} icon="icon-[tabler--user-check]" />
      </div>

      {error && (
        <div className="alert alert-error alert-soft" role="alert">
          {error}
        </div>
      )}

      <div className="card shadow-base-300/10 shadow-md">
        <div className="card-header flex flex-wrap items-center gap-3">
          <div className="join">
            {SCOPES.map((s) => (
              <button
                key={s.key}
                type="button"
                className={`join-item btn btn-sm ${scope === s.key ? "btn-primary" : "btn-soft"}`}
                onClick={() => reset(() => setScope(s.key))}
              >
                <span className={`${s.icon} size-4`} />
                {s.label}
              </button>
            ))}
          </div>
          <label className="input input-sm max-w-52">
            <span className="icon-[tabler--search] text-base-content/60 my-auto size-4 shrink-0" />
            <input
              type="search"
              className="grow"
              placeholder={`Search ${scope}s…`}
              value={search}
              onChange={(e) => reset(() => setSearch(e.target.value))}
            />
          </label>
          <div className="join">
            {ASSIGNED.map((a) => (
              <button
                key={a.key}
                type="button"
                className={`join-item btn btn-sm ${assigned === a.key ? "btn-primary" : "btn-soft"}`}
                onClick={() => reset(() => setAssigned(a.key))}
              >
                {a.label}
              </button>
            ))}
          </div>
          {scope === "file" && (
            <div className="join">
              {ENTITY_TYPES.map((e) => (
                <button
                  key={e.key}
                  type="button"
                  className={`join-item btn btn-sm ${entity === e.key ? "btn-primary" : "btn-soft"}`}
                  onClick={() => reset(() => setEntity(e.key))}
                >
                  <span className={`${e.icon} size-4`} />
                  {e.label}
                </button>
              ))}
            </div>
          )}
          <select
            className="select select-sm ms-auto max-w-36"
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
          <PerPage value={perPage} onChange={(n) => reset(() => setPerPage(n))} disabled={busy} />
        </div>

        {/* Batch assignment menu — acts on the checked rows. */}
        <div className="card-header border-base-content/10 flex flex-wrap items-center gap-3 border-t">
          <span className="text-sm">
            <span className="font-medium">{selected.size}</span> selected
          </span>
          <AssignCombobox
            active={null}
            options={validNames}
            disabled={busy || selected.size === 0}
            onAssign={batchAssign}
            onClear={() => undefined}
          />
          <button
            type="button"
            className="btn btn-soft btn-sm"
            disabled={busy || selected.size === 0}
            onClick={batchClear}
          >
            <span className="icon-[tabler--x] size-4" />
            Clear assignment
          </button>
          {assigned === "ignored" ? (
            <button
              type="button"
              className="btn btn-soft btn-sm"
              title="Restore — return to triage"
              disabled={busy || selected.size === 0}
              onClick={batchRestore}
            >
              <span className="icon-[tabler--eye] size-4" />
              Restore
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-soft btn-sm"
              title="Ignore — remove from triage and scraping"
              disabled={busy || selected.size === 0}
              onClick={batchIgnore}
            >
              <span className="icon-[tabler--eye-off] size-4" />
              Ignore
            </button>
          )}
          <button
            type="button"
            className="btn btn-ghost btn-sm ms-auto"
            disabled={busy || selected.size === 0}
            onClick={() => setSelected(new Set())}
          >
            Deselect all
          </button>
        </div>

        <div className="card-body p-0">
          <div>
            <table className="table">
              <thead>
                <tr>
                  <th className="w-0">
                    <input
                      ref={selectAllRef}
                      type="checkbox"
                      className="checkbox checkbox-sm"
                      aria-label="Select all"
                      checked={allChecked}
                      onChange={toggleAll}
                    />
                  </th>
                  <th className="w-0"></th>
                  <th>Name</th>
                  <th className="text-center">Files</th>
                  <th>Assignment</th>
                </tr>
              </thead>
              <tbody>
                {assets.map((a) => (
                  <tr key={a.asset_id} className={selected.has(a.asset_id) ? "bg-base-200/50" : undefined}>
                    <td>
                      <input
                        type="checkbox"
                        className="checkbox checkbox-sm"
                        aria-label={`Select ${a.basename ?? a.asset_id}`}
                        checked={selected.has(a.asset_id)}
                        onChange={() => toggleOne(a.asset_id)}
                      />
                    </td>
                    <td>
                      {a.thumb_stash_id ? (
                        <div className="avatar">
                          <div className="rounded-field size-10 overflow-hidden">
                            <img
                              src={`${api.base}/thumbnail/${a.thumb_stash_id}`}
                              alt=""
                              loading="lazy"
                            />
                          </div>
                        </div>
                      ) : (
                        <div className="avatar avatar-placeholder">
                          <div className="bg-base-200 text-base-content/40 rounded-field size-10">
                            <span className="icon-[tabler--photo] size-5" />
                          </div>
                        </div>
                      )}
                    </td>
                    <td>
                      <div className="flex flex-col">
                        <span className="font-medium">{a.basename ?? `#${a.asset_id}`}</span>
                        {a.path && (
                          <span className="text-base-content/40 max-w-lg truncate text-xs">
                            {a.path}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="text-center">
                      {a.child_count > 0 ? (
                        a.child_count
                      ) : (
                        <span className="text-base-content/40">—</span>
                      )}
                    </td>
                    <td>
                      {a.ignored ? (
                        <div className="flex items-center gap-2">
                          <span className="badge badge-sm badge-soft text-base-content/60">
                            <span className="icon-[tabler--eye-off] size-3.5" />
                            Ignored
                          </span>
                          <button
                            type="button"
                            className="btn btn-xs btn-soft"
                            title="Restore — return to triage"
                            disabled={busy}
                            onClick={() => void withBusy(() => api.unignore(a.asset_id))}
                          >
                            <span className="icon-[tabler--eye] size-4" />
                            Restore
                          </button>
                        </div>
                      ) : (
                        <AssignCombobox
                          active={a.active}
                          options={validNames}
                          disabled={busy}
                          onAssign={(id) =>
                            void withBusy(() => api.activate(a.asset_id, id, scope))
                          }
                          onClear={() => void withBusy(() => api.deactivate(a.asset_id))}
                          onIgnore={() => void withBusy(() => api.ignore(a.asset_id))}
                        />
                      )}
                    </td>
                  </tr>
                ))}
                {assets.length === 0 && (
                  <tr>
                    <td colSpan={5} className="text-base-content/50 py-8 text-center">
                      No {scope} assets.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <Pager total={total} offset={offset} page={perPage} busy={busy} onOffset={setOffset} />
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
      <div className="stat-title capitalize">{label}</div>
      <div className="stat-value text-2xl">{value}</div>
    </div>
  );
}
