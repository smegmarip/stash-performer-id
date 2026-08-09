import { useCallback, useEffect, useState } from "react";

import { api } from "./lib/api";
import type { NameRow, Summary } from "./lib/api";

const FILTERS = [
  { key: "valid", label: "Valid" },
  { key: "invalid", label: "Invalid" },
  { key: "", label: "All" },
] as const;

export default function NamesView() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [names, setNames] = useState<NameRow[]>([]);
  const [filter, setFilter] = useState<string>("valid");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [s, n] = await Promise.all([api.summary(), api.listNames(filter || undefined)]);
      setSummary(s);
      setNames(n);
      setSelected(new Set());
    } catch (e) {
      setError(String(e));
    }
  }, [filter]);

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
  const toggleAll = () =>
    setSelected(allSelected ? new Set() : new Set(names.map((n) => n.id)));

  return (
    <>
      {error && <div className="error">{error}</div>}

      <section className="summary">
        {summary ? (
          <>
            <Stat label="assets" value={summary.assets} />
            <Stat label="candidates" value={summary.candidates} />
            <Stat label="distinct names" value={summary.distinct_names} />
          </>
        ) : (
          <span>loading…</span>
        )}
        <div className="spacer" />
        <button disabled={busy} onClick={() => void withBusy(api.harvestGalleries)}>
          Harvest galleries
        </button>
        <button disabled={busy} onClick={() => void refresh()}>
          Refresh
        </button>
      </section>

      <section className="add">
        <input
          placeholder="Add a name (direct input)…"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addName()}
        />
        <button disabled={busy || !newName.trim()} onClick={addName}>
          Add
        </button>
      </section>

      <nav className="tabs">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={filter === f.key ? "active" : ""}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
        <div className="spacer" />
        {selected.size > 0 && (
          <div className="batch">
            <span>{selected.size} selected</span>
            <button disabled={busy} onClick={() => void batchSetValid(false)}>
              Invalidate
            </button>
            <button disabled={busy} onClick={() => void batchSetValid(true)}>
              Validate
            </button>
            <button disabled={busy} onClick={() => setSelected(new Set())}>
              Clear
            </button>
          </div>
        )}
      </nav>

      <table>
        <thead>
          <tr>
            <th className="cb">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={toggleAll}
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
                <input
                  defaultValue={row.name}
                  onBlur={(e) => saveField(row, "name", e.target.value)}
                />
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
