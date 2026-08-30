import { useEffect, useState } from "react";

import { api, isEmptyField, PROFILE_FIELDS } from "../lib/api";
import type { EnrichProfile } from "../lib/api";

// Read-only view of a name's resolved enrichment profile — the only way to inspect it. Opened by
// clicking the enrichment thumbnail. Shows populated fields + per-field source, and an image
// carousel. No toggles, no save.

function display(value: unknown): string {
  return Array.isArray(value) ? value.join(", ") : String(value);
}

function DetailsText({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const long = text.length > 280;
  return (
    <div>
      <p className={`whitespace-pre-wrap ${expanded ? "" : "line-clamp-4"}`}>{text}</p>
      {long && (
        <button
          type="button"
          className="link link-primary text-xs"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  );
}

export function ProfileModal({
  nameId,
  name,
  onClose,
}: {
  nameId: number;
  name: string;
  onClose: () => void;
}) {
  const [profile, setProfile] = useState<EnrichProfile>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [imgIdx, setImgIdx] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api
      .enrichProfile(nameId)
      .then((r) => {
        if (!cancelled) {
          setProfile(r.profile);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(String(e));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [nameId]);

  const p = profile as Record<string, unknown> | null;
  const images = (p?.images as string[] | undefined) ?? [];
  const sources = (profile?.field_sources ?? {}) as Record<string, string>;
  const fields = p
    ? PROFILE_FIELDS.map((f) => ({ field: f as string, value: p[f] })).filter(
        ({ value }) => !isEmptyField(value),
      )
    : [];

  const valueCell = (field: string, value: unknown) => {
    if (field === "urls" && Array.isArray(value)) {
      return (
        <ul className="list-disc ps-4">
          {value.map((u, i) => (
            <li key={i} className="truncate">
              <a href={String(u)} target="_blank" rel="noreferrer" className="link link-primary">
                {String(u)}
              </a>
            </li>
          ))}
        </ul>
      );
    }
    if (field === "details") return <DetailsText text={String(value)} />;
    if (field === "custom_fields" && typeof value === "object" && value) {
      return (
        <dl className="space-y-0.5">
          {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
            <div key={k} className="flex gap-1">
              <dt className="text-base-content/60 shrink-0 capitalize">{k.replace(/_/g, " ")}:</dt>
              <dd className="break-words">{String(v)}</dd>
            </div>
          ))}
        </dl>
      );
    }
    return display(value);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4">
      <div className="bg-base-100 rounded-box mt-8 w-full max-w-3xl shadow-xl">
        <div className="border-base-content/10 flex items-center justify-between border-b px-4 py-3">
          <div>
            <h3 className="font-semibold">{name}</h3>
            <span className="text-base-content/50 text-xs">enrichment profile</span>
          </div>
          <button type="button" className="btn btn-text btn-circle btn-sm" onClick={onClose}>
            <span className="icon-[tabler--x] size-5" />
          </button>
        </div>

        <div className="p-4">
          {loading ? (
            <div className="text-base-content/50 py-10 text-center">
              <span className="loading loading-spinner" /> Loading…
            </div>
          ) : error ? (
            <div className="alert alert-error alert-soft" role="alert">
              {error}
            </div>
          ) : !profile || fields.length === 0 ? (
            <div className="text-base-content/50 py-8 text-center text-sm">No resolved profile.</div>
          ) : (
            <div className="grid grid-cols-1 gap-6 md:grid-cols-12">
              <div className="space-y-1.5 md:col-span-7">
                {fields.map(({ field, value }) => (
                  <div key={field} className="grid grid-cols-12 items-start gap-2">
                    <strong className="col-span-4 capitalize">{field.replace(/_/g, " ")}:</strong>
                    <div className="col-span-8 break-words text-sm">
                      {valueCell(field, value)}
                      {sources[field] && (
                        <span className="text-base-content/40 ml-2 text-xs">({sources[field]})</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              {images.length > 0 && (
                <div className="md:col-span-5">
                  <div className="bg-base-200 rounded-box overflow-hidden">
                    <img
                      src={images[imgIdx]}
                      alt=""
                      className="h-72 w-full object-contain"
                      loading="lazy"
                    />
                  </div>
                  {images.length > 1 && (
                    <div className="mt-3 flex items-center gap-2">
                      <button
                        type="button"
                        className="btn btn-soft btn-sm btn-square"
                        onClick={() => setImgIdx((i) => (i - 1 + images.length) % images.length)}
                      >
                        <span className="icon-[tabler--arrow-left] size-4" />
                      </button>
                      <span className="text-base-content/70 grow text-center text-sm">
                        {imgIdx + 1} of {images.length}
                      </span>
                      <button
                        type="button"
                        className="btn btn-soft btn-sm btn-square"
                        onClick={() => setImgIdx((i) => (i + 1) % images.length)}
                      >
                        <span className="icon-[tabler--arrow-right] size-4" />
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
