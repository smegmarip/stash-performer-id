import { useCallback, useEffect, useState } from "react";

import { api } from "./lib/api";
import type { GalleryAsset } from "./lib/api";

export default function GalleriesView() {
  const [galleries, setGalleries] = useState<GalleryAsset[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [onlyUnassigned, setOnlyUnassigned] = useState(false);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      setGalleries(await api.listGalleries());
    } catch (e) {
      setError(String(e));
    }
  }, []);

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

  const rows = onlyUnassigned ? galleries.filter((g) => !g.active) : galleries;
  const assignedCount = galleries.filter((g) => g.active).length;

  return (
    <>
      {error && <div className="error">{error}</div>}

      <section className="summary">
        <Stat label="galleries" value={galleries.length} />
        <Stat label="assigned" value={assignedCount} />
        <div className="spacer" />
        <label className="toggle">
          <input
            type="checkbox"
            checked={onlyUnassigned}
            onChange={(e) => setOnlyUnassigned(e.target.checked)}
          />
          unassigned only
        </label>
        <button disabled={busy} onClick={() => void refresh()}>
          Refresh
        </button>
      </section>

      <div className="galleries">
        {rows.map((g) => (
          <div key={g.asset_id} className="gallery">
            <div className="gallery-head">
              <b>{g.basename ?? g.path ?? `#${g.asset_id}`}</b>
              {g.active ? (
                <span className="active-name">
                  {g.active.name}
                  <button
                    className="clear"
                    disabled={busy}
                    title="clear assignment"
                    onClick={() => void withBusy(() => api.deactivate(g.asset_id))}
                  >
                    ×
                  </button>
                </span>
              ) : (
                <span className="muted">unassigned</span>
              )}
            </div>
            <div className="chips">
              {g.candidates.map((c) => {
                const active = g.active?.name_id === c.name_id;
                const cls = `chip${active ? " active" : ""}${c.valid ? "" : " invalid"}`;
                return (
                  <button
                    key={c.name_id}
                    className={cls}
                    disabled={busy}
                    title={c.valid ? "activate" : "invalid name"}
                    onClick={() => void withBusy(() => api.activate(g.asset_id, c.name_id))}
                  >
                    {c.name}
                  </button>
                );
              })}
              {g.candidates.length === 0 && <span className="muted">no candidates</span>}
            </div>
          </div>
        ))}
        {rows.length === 0 && <div className="empty">No galleries.</div>}
      </div>
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
