import { useCallback, useEffect, useState } from "react";

import { api } from "./lib/api";
import type { AssetRow, NameRow, Scope } from "./lib/api";
import { useDebounced } from "./lib/useDebounced";
import { Pager } from "./ui/Pager";

const SCOPES: { key: Scope; label: string }[] = [
  { key: "gallery", label: "Gallery" },
  { key: "folder", label: "Folder" },
  { key: "file", label: "File" },
];
const ASSIGNED = [
  { key: "", label: "All" },
  { key: "assigned", label: "Assigned" },
  { key: "unassigned", label: "Unassigned" },
] as const;
const SORTS = [
  { key: "path", label: "Path" },
  { key: "name", label: "Name" },
] as const;
const PAGE = 100;

export default function AssetsView() {
  const [scope, setScope] = useState<Scope>("gallery");
  const [assets, setAssets] = useState<AssetRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("path");
  const [order, setOrder] = useState("asc");
  const [assigned, setAssigned] = useState("");
  const [validNames, setValidNames] = useState<NameRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const q = useDebounced(search);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [page, names] = await Promise.all([
        api.listAssets({ type: scope, q, sort, order, assigned: assigned || undefined, limit: PAGE, offset }),
        api.listNames({ status: "valid", limit: 1000 }),
      ]);
      setAssets(page.assets);
      setTotal(page.total);
      setValidNames(names.names);
    } catch (e) {
      setError(String(e));
    }
  }, [scope, q, sort, order, assigned, offset]);

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
    <>
      {error && <div className="error">{error}</div>}

      <div className="statusbar">
        <Stat label={scope} value={total} />
        <Stat label="assigned (page)" value={assignedCount} />
      </div>

      <div className="toolbar">
        <div className="filters">
          <div className="seg">
            {SCOPES.map((s) => (
              <button
                key={s.key}
                className={scope === s.key ? "active" : ""}
                onClick={() => reset(() => setScope(s.key))}
              >
                {s.label}
              </button>
            ))}
          </div>
          <input
            className="search"
            placeholder={`Search ${scope}s…`}
            value={search}
            onChange={(e) => reset(() => setSearch(e.target.value))}
          />
          <div className="seg">
            {ASSIGNED.map((a) => (
              <button
                key={a.key}
                className={assigned === a.key ? "active" : ""}
                onClick={() => reset(() => setAssigned(a.key))}
              >
                {a.label}
              </button>
            ))}
          </div>
          <select className="sortsel" value={sort} onChange={(e) => setSort(e.target.value)}>
            {SORTS.map((s) => (
              <option key={s.key} value={s.key}>
                sort: {s.label}
              </option>
            ))}
          </select>
          <button title="toggle order" onClick={() => setOrder(order === "asc" ? "desc" : "asc")}>
            {order === "asc" ? "↑" : "↓"}
          </button>
        </div>
        <div className="actions">
          <button disabled={busy} onClick={() => void refresh()}>
            Refresh
          </button>
        </div>
      </div>

      <div className="assets">
        {assets.map((a) => (
          <div key={a.asset_id} className="asset">
            <div className="asset-head">
              <b>{a.basename ?? a.path ?? `#${a.asset_id}`}</b>
              {a.child_count > 0 && <span className="count">{a.child_count} images</span>}
              {a.active ? (
                <span className="active-name">
                  {a.active.name}
                  <span className="cascade">
                    {" "}
                    (from {a.active.source_level}
                    {a.child_count > 0 ? ` → ${a.child_count}` : ""})
                  </span>
                  <button
                    className="clear"
                    disabled={busy}
                    title="clear assignment (and its images)"
                    onClick={() => void withBusy(() => api.deactivate(a.asset_id))}
                  >
                    ×
                  </button>
                </span>
              ) : (
                <span className="muted">unassigned</span>
              )}
            </div>
            <div className="chips">
              {a.candidates.map((c) => {
                const active = a.active?.name_id === c.name_id;
                const cls = `chip${active ? " active" : ""}${c.valid ? "" : " invalid"}`;
                return (
                  <button
                    key={c.name_id}
                    className={cls}
                    disabled={busy}
                    onClick={() => void withBusy(() => api.activate(a.asset_id, c.name_id, scope))}
                  >
                    {c.name}
                  </button>
                );
              })}
              <select
                className="assign"
                disabled={busy}
                value=""
                onChange={(e) => {
                  const id = Number(e.target.value);
                  if (id) void withBusy(() => api.activate(a.asset_id, id, scope));
                }}
              >
                <option value="">assign name…</option>
                {validNames.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
        ))}
        {assets.length === 0 && <div className="empty">No {scope} assets.</div>}
      </div>

      <Pager total={total} offset={offset} page={PAGE} busy={busy} onOffset={setOffset} />
    </>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="stat">
      <b>{value}</b>
      <span>{label}</span>
    </div>
  );
}
