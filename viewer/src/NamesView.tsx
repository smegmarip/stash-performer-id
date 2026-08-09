import { useCallback, useEffect, useState } from "react";

import { api } from "./lib/api";
import type { NameRow, Summary } from "./lib/api";
import { useDebounced } from "./lib/useDebounced";
import { Pager } from "./ui/Pager";

const FILTERS = [
  { key: "valid", label: "Valid" },
  { key: "invalid", label: "Invalid" },
  { key: "", label: "All" },
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
  const [filter, setFilter] = useState<string>("valid");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("name");
  const [order, setOrder] = useState("asc");
  const [offset, setOffset] = useState(0);
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
        api.listNames({ status: filter || undefined, q, sort, order, limit: PAGE, offset }),
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
    <>
      {error && <div className="error">{error}</div>}

      <div className="statusbar">
        {summary && (
          <>
            <Stat label="assets" value={summary.assets} />
            <Stat label="candidates" value={summary.candidates} />
            <Stat label="distinct names" value={summary.distinct_names} />
          </>
        )}
        <Stat label={`${filter || "all"} shown`} value={total} />
      </div>

      <div className="toolbar">
        <div className="filters">
          <div className="seg">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                className={filter === f.key ? "active" : ""}
                onClick={() => reset(() => setFilter(f.key))}
              >
                {f.label}
              </button>
            ))}
          </div>
          <input
            className="search"
            placeholder="Search names…"
            value={search}
            onChange={(e) => reset(() => setSearch(e.target.value))}
          />
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
          {selected.size > 0 && (
            <span className="batch">
              <span>{selected.size} selected</span>
              <button disabled={busy} onClick={() => void batchSetValid(false)}>
                Invalidate
              </button>
              <button disabled={busy} onClick={() => void batchSetValid(true)}>
                Validate
              </button>
            </span>
          )}
          <input
            className="search"
            placeholder="Add name…"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addName()}
          />
          <button disabled={busy || !newName.trim()} onClick={addName}>
            Add
          </button>
          <button disabled={busy} onClick={() => void withBusy(api.harvestGalleries)}>
            Harvest
          </button>
          <button disabled={busy} onClick={() => void refresh()}>
            Refresh
          </button>
        </div>
      </div>

      <table>
        <thead>
          <tr>
            <th className="cb">
              <input
                type="checkbox"
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
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {names.map((row) => (
            <tr key={row.id} className={row.valid ? "valid" : "invalid"}>
              <td className="cb">
                <input
                  type="checkbox"
                  checked={selected.has(row.id)}
                  onChange={() => toggle(row.id)}
                  aria-label={`select ${row.name}`}
                />
              </td>
              <td>
                <input defaultValue={row.name} onBlur={(e) => saveField(row, "name", e.target.value)} />
              </td>
              <td>
                <input
                  defaultValue={row.disambiguation}
                  placeholder="—"
                  onBlur={(e) => saveField(row, "disambiguation", e.target.value)}
                />
              </td>
              <td>
                <span className={`badge ${row.valid ? "valid" : "invalid"}`}>
                  {row.valid ? "valid" : "invalid"}
                </span>
              </td>
              <td className="actions">
                {row.valid ? (
                  <button title="invalidate" onClick={() => void setValid(row, false)}>
                    ✗
                  </button>
                ) : (
                  <button title="validate" onClick={() => void setValid(row, true)}>
                    ✓
                  </button>
                )}
              </td>
            </tr>
          ))}
          {names.length === 0 && (
            <tr>
              <td colSpan={5} className="empty">
                No names.
              </td>
            </tr>
          )}
        </tbody>
      </table>

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
