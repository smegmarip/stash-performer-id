import { useCallback, useEffect, useState } from "react";

import { api } from "./lib/api";
import type { AssetRow, NameRow, Scope } from "./lib/api";
import { useDebounced } from "./lib/useDebounced";
import { useUrlNumber, useUrlState } from "./lib/useUrlState";
import { AssignCombobox } from "./ui/AssignCombobox";
import { Pager } from "./ui/Pager";

const SCOPES: { key: Scope; label: string; icon: string }[] = [
  { key: "gallery", label: "Gallery", icon: "icon-[tabler--photo]" },
  { key: "folder", label: "Folder", icon: "icon-[tabler--folder]" },
  { key: "file", label: "File", icon: "icon-[tabler--file]" },
];
const ASSIGNED = [
  { key: "all", label: "All" },
  { key: "assigned", label: "Assigned" },
  { key: "unassigned", label: "Unassigned" },
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
const PAGE = 100;

export default function AssetsView() {
  const [scopeStr, setScope] = useUrlState("scope", "gallery");
  const scope = scopeStr as Scope;
  const [assets, setAssets] = useState<AssetRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useUrlNumber("offset", 0);
  const [search, setSearch] = useUrlState("q", "");
  const [sort, setSort] = useUrlState("sort", "path");
  const [order, setOrder] = useUrlState("order", "asc");
  const [assigned, setAssigned] = useUrlState("assigned", "all");
  const [entity, setEntity] = useUrlState("entity", "image");
  const [validNames, setValidNames] = useState<NameRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const q = useDebounced(search);

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
          limit: PAGE,
          offset,
        }),
        api.listNames({ status: "valid", limit: 1000 }),
      ]);
      setAssets(page.assets);
      setTotal(page.total);
      setValidNames(names.names);
    } catch (e) {
      setError(String(e));
    }
  }, [scope, q, sort, order, assigned, entity, offset]);

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
        </div>

        <div className="card-body p-0">
          <div>
            <table className="table">
              <thead>
                <tr>
                  <th className="w-0"></th>
                  <th>Name</th>
                  <th className="text-center">Images</th>
                  <th>Assignment</th>
                </tr>
              </thead>
              <tbody>
                {assets.map((a) => (
                  <tr key={a.asset_id}>
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
                          <span className="text-base-content/40 max-w-md truncate text-xs">
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
                      <AssignCombobox
                        active={a.active}
                        options={validNames}
                        disabled={busy}
                        onAssign={(id) => void withBusy(() => api.activate(a.asset_id, id, scope))}
                        onClear={() => void withBusy(() => api.deactivate(a.asset_id))}
                      />
                    </td>
                  </tr>
                ))}
                {assets.length === 0 && (
                  <tr>
                    <td colSpan={4} className="text-base-content/50 py-8 text-center">
                      No {scope} assets.
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
      <div className="stat-title capitalize">{label}</div>
      <div className="stat-value text-2xl">{value}</div>
    </div>
  );
}
