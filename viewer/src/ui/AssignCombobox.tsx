import { useEffect, useRef, useState } from "react";

import type { NameRow } from "../lib/api";

type Active = { name_id: number; name: string; source_level: string } | null;

export function AssignCombobox({
  active,
  options,
  disabled,
  onAssign,
  onClear,
  onIgnore,
}: {
  active: Active;
  options: NameRow[];
  disabled: boolean;
  onAssign: (nameId: number) => void;
  onClear: () => void;
  onIgnore?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const q = query.trim().toLowerCase();
  const filtered = q ? options.filter((n) => n.name.toLowerCase().includes(q)) : options;

  const pick = (nameId: number) => {
    setOpen(false);
    setQuery("");
    onAssign(nameId);
  };

  return (
    <div className="relative w-64" ref={ref}>
      <button
        type="button"
        className="input input-sm flex w-full items-center justify-between gap-2 cursor-pointer"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
      >
        {active ? (
          <span className="truncate">
            <span className="text-base-content/50">{active.source_level}:</span> {active.name}
          </span>
        ) : (
          <span className="text-base-content/50">assign…</span>
        )}
        <span className="icon-[tabler--chevron-down] text-base-content/60 size-4 shrink-0" />
      </button>

      {open && (
        <div className="bg-base-100 border-base-content/20 rounded-box absolute z-30 mt-1 w-full border shadow-lg">
          <div className="flex items-center gap-1 p-2">
            <input
              className="input input-sm w-full"
              placeholder="Search names…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoFocus
            />
            {onIgnore && (
              <button
                type="button"
                className="btn btn-sm btn-square btn-text text-base-content/60 shrink-0"
                title="Ignore — remove from triage and scraping"
                onClick={() => {
                  setOpen(false);
                  onIgnore();
                }}
              >
                <span className="icon-[tabler--eye-off] size-4" />
              </button>
            )}
          </div>
          <ul className="menu menu-sm max-h-64 w-full flex-nowrap overflow-y-auto p-1 pt-0">
            {active && (
              <li>
                <button
                  type="button"
                  className="text-error"
                  onClick={() => {
                    setOpen(false);
                    onClear();
                  }}
                >
                  <span className="icon-[tabler--x] size-4" />
                  Clear assignment
                </button>
              </li>
            )}
            {filtered.map((n) => (
              <li key={n.id}>
                <button
                  type="button"
                  className={active?.name_id === n.id ? "menu-active" : ""}
                  onClick={() => pick(n.id)}
                >
                  {n.name}
                </button>
              </li>
            ))}
            {filtered.length === 0 && (
              <li className="text-base-content/50 px-2 py-1.5 text-sm">No matches</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
