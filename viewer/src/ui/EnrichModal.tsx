import { useCallback, useEffect, useMemo, useState } from "react";

import { api, LIST_FIELDS, PROFILE_FIELDS } from "../lib/api";
import type { Candidate, FieldApply } from "../lib/api";

// The scene-tagger resolve flow, in the viewer's stack: candidate grid → per-field ✓/✕ + an
// image carousel → apply the checked fields onto the resolved profile (docs/ENRICHMENT.md §5).

function fieldDisplay(value: unknown): string {
  return Array.isArray(value) ? value.join(", ") : String(value);
}

// The candidate's populated fields (superset filtered to non-empty) — only these are shown.
function populated(c: Candidate): { field: string; value: unknown }[] {
  const out: { field: string; value: unknown }[] = [];
  for (const f of PROFILE_FIELDS) {
    const v = c.data[f];
    if (v == null || v === "" || (Array.isArray(v) && v.length === 0)) continue;
    out.push({ field: f, value: v });
  }
  return out;
}

function ResolvePane({
  candidate,
  source,
  busy,
  onBack,
  onApply,
}: {
  candidate: Candidate;
  source: string;
  busy: boolean;
  onBack: () => void;
  onApply: (fields: Record<string, FieldApply>) => void;
}) {
  const fields = useMemo(() => populated(candidate), [candidate]);
  const [included, setIncluded] = useState<Set<string>>(() => new Set(fields.map((f) => f.field)));
  const images = (candidate.data.images ?? []) as string[];
  const [imgIdx, setImgIdx] = useState(0);
  const [imgIncluded, setImgIncluded] = useState(images.length > 0);

  const toggle = (f: string) =>
    setIncluded((prev) => {
      const next = new Set(prev);
      if (next.has(f)) next.delete(f);
      else next.add(f);
      return next;
    });

  const apply = () => {
    const payload: Record<string, FieldApply> = {};
    for (const { field, value } of fields) {
      if (included.has(field)) payload[field] = { value, source };
    }
    if (imgIncluded && images.length) payload["images"] = { value: [images[imgIdx]], source };
    onApply(payload);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <button type="button" className="btn btn-text btn-sm" onClick={onBack}>
          <span className="icon-[tabler--arrow-left] size-4" />
          Candidates
        </button>
        <span className="text-base-content/60 text-sm">
          {source} · {candidate.data.name}
        </span>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-[1fr_auto]">
        {/* Fields with per-field include toggles */}
        <ul className="divide-base-content/10 divide-y">
          {fields.map(({ field, value }) => {
            const on = included.has(field);
            return (
              <li key={field} className="flex items-start gap-3 py-1.5">
                <button
                  type="button"
                  className={`btn btn-circle btn-xs mt-0.5 ${on ? "btn-success" : "btn-error btn-soft"}`}
                  title={on ? "included" : "excluded"}
                  onClick={() => toggle(field)}
                >
                  <span className={`${on ? "icon-[tabler--check]" : "icon-[tabler--x]"} size-3.5`} />
                </button>
                <div className={`min-w-0 grow ${on ? "" : "opacity-40"}`}>
                  <div className="text-base-content/50 text-xs capitalize">
                    {field.replace(/_/g, " ")}
                    {LIST_FIELDS.has(field) && <span className="ms-1">(list)</span>}
                  </div>
                  <div className="break-words text-sm">{fieldDisplay(value)}</div>
                </div>
              </li>
            );
          })}
          {fields.length === 0 && (
            <li className="text-base-content/50 py-4 text-sm">This candidate has no fields.</li>
          )}
        </ul>

        {/* Image carousel */}
        {images.length > 0 && (
          <div className="w-full md:w-56">
            <div className="bg-base-200 rounded-box relative overflow-hidden">
              <img
                src={images[imgIdx]}
                alt=""
                className={`h-56 w-full object-cover ${imgIncluded ? "" : "opacity-30"}`}
                loading="lazy"
              />
              <button
                type="button"
                className={`btn btn-circle btn-xs absolute end-1 top-1 ${imgIncluded ? "btn-success" : "btn-error btn-soft"}`}
                title={imgIncluded ? "image included" : "image excluded"}
                onClick={() => setImgIncluded((v) => !v)}
              >
                <span className={`${imgIncluded ? "icon-[tabler--check]" : "icon-[tabler--x]"} size-3.5`} />
              </button>
            </div>
            {images.length > 1 && (
              <div className="mt-1 flex items-center justify-between">
                <button
                  type="button"
                  className="btn btn-soft btn-xs"
                  onClick={() => setImgIdx((i) => (i - 1 + images.length) % images.length)}
                >
                  <span className="icon-[tabler--chevron-left] size-4" />
                </button>
                <span className="text-base-content/50 text-xs">
                  {imgIdx + 1} / {images.length}
                </span>
                <button
                  type="button"
                  className="btn btn-soft btn-xs"
                  onClick={() => setImgIdx((i) => (i + 1) % images.length)}
                >
                  <span className="icon-[tabler--chevron-right] size-4" />
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="flex justify-end gap-2">
        <button type="button" className="btn btn-soft btn-sm" onClick={onBack} disabled={busy}>
          Cancel
        </button>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={apply}
          disabled={busy || (included.size === 0 && !imgIncluded)}
        >
          Apply {included.size + (imgIncluded && images.length ? 1 : 0)} field(s)
        </button>
      </div>
    </div>
  );
}

export function EnrichModal({
  nameId,
  name,
  source,
  onClose,
  onApplied,
}: {
  nameId: number;
  name: string;
  source: string;
  onClose: () => void;
  onApplied: () => void;
}) {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState<Candidate | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (refresh = false) => {
      setLoading(true);
      setError(null);
      try {
        const r = await api.enrichCandidates(nameId, source, refresh);
        setCandidates(r.candidates);
        setError(r.error);
      } catch (e) {
        setError(String(e));
      } finally {
        setLoading(false);
      }
    },
    [nameId, source],
  );
  useEffect(() => {
    void load();
  }, [load]);

  const apply = async (fields: Record<string, FieldApply>) => {
    setBusy(true);
    setError(null);
    try {
      await api.applyProfile(nameId, fields);
      onApplied();
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4">
      <div className="bg-base-100 rounded-box mt-8 w-full max-w-3xl shadow-xl">
        <div className="border-base-content/10 flex items-center justify-between border-b px-4 py-3">
          <div>
            <h3 className="font-semibold">Enrich: {name}</h3>
            <span className="text-base-content/50 text-xs">source: {source}</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="btn btn-soft btn-sm"
              onClick={() => void load(true)}
              disabled={loading}
              title="Re-search live (ignore cache)"
            >
              <span className="icon-[tabler--refresh] size-4" />
              Re-search
            </button>
            <button type="button" className="btn btn-text btn-circle btn-sm" onClick={onClose}>
              <span className="icon-[tabler--x] size-5" />
            </button>
          </div>
        </div>

        <div className="p-4">
          {error && (
            <div className="alert alert-error alert-soft mb-3" role="alert">
              {error}
            </div>
          )}
          {loading ? (
            <div className="text-base-content/50 py-10 text-center">
              <span className="loading loading-spinner" /> Searching {source}…
            </div>
          ) : selected ? (
            <ResolvePane
              candidate={selected}
              source={source}
              busy={busy}
              onBack={() => setSelected(null)}
              onApply={apply}
            />
          ) : candidates.length === 0 ? (
            <div className="text-base-content/50 py-10 text-center">
              No candidates on {source}. Try Re-search or another source.
            </div>
          ) : (
            <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {candidates.map((c) => {
                const img = (c.data.images ?? [])[0] as string | undefined;
                return (
                  <li key={`${c.source}-${c.source_entity_id}`}>
                    <button
                      type="button"
                      className="border-base-content/10 hover:bg-base-200 flex w-full items-center gap-3 rounded-box border p-2 text-start"
                      onClick={() => setSelected(c)}
                    >
                      <div className="bg-base-200 size-12 shrink-0 overflow-hidden rounded">
                        {img ? (
                          <img src={img} alt="" className="size-full object-cover" loading="lazy" />
                        ) : (
                          <span className="icon-[tabler--user] text-base-content/30 m-3 size-6" />
                        )}
                      </div>
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium">{c.data.name}</div>
                        {c.data.disambiguation != null && (
                          <div className="text-base-content/50 truncate text-xs">
                            {String(c.data.disambiguation)}
                          </div>
                        )}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
