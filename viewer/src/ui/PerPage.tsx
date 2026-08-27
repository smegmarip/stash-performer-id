import { useUrlNumber } from "../lib/useUrlState";

export const PER_PAGE_OPTIONS = [25, 50, 100, 200] as const;

/** Page size, persisted in the `perPage` URL param and clamped to the offered options (so a
 * shared link with an out-of-set value falls back to the view's default rather than showing a
 * value the <select> can't render). */
export function usePerPage(defaultValue: number): [number, (v: number) => void] {
  const [raw, set] = useUrlNumber("perPage", defaultValue);
  const value = (PER_PAGE_OPTIONS as readonly number[]).includes(raw) ? raw : defaultValue;
  return [value, set];
}

export function PerPage({
  value,
  onChange,
  disabled,
}: {
  value: number;
  onChange: (v: number) => void;
  disabled?: boolean;
}) {
  return (
    <select
      className="select select-sm max-w-28"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(Number(e.target.value))}
      title="Results per page"
    >
      {PER_PAGE_OPTIONS.map((n) => (
        <option key={n} value={n}>
          {n} / page
        </option>
      ))}
    </select>
  );
}
