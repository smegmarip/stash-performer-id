export function Pager({
  total,
  offset,
  page,
  busy,
  onOffset,
  alwaysShow = false,
}: {
  total: number;
  offset: number;
  page: number;
  busy: boolean;
  onOffset: (offset: number) => void;
  // Show the range label (with disabled arrows) even when everything fits on one page.
  alwaysShow?: boolean;
}) {
  if (total === 0 || (total <= page && !alwaysShow)) return null;
  return (
    <div className="text-base-content/60 flex items-center justify-center gap-3 py-3 text-sm">
      <button
        type="button"
        className="btn btn-soft btn-sm"
        disabled={busy || offset === 0}
        onClick={() => onOffset(Math.max(0, offset - page))}
      >
        <span className="icon-[tabler--chevron-left] size-4" />
        Prev
      </button>
      <span>
        {offset + 1}–{Math.min(offset + page, total)} of {total}
      </span>
      <button
        type="button"
        className="btn btn-soft btn-sm"
        disabled={busy || offset + page >= total}
        onClick={() => onOffset(offset + page)}
      >
        Next
        <span className="icon-[tabler--chevron-right] size-4" />
      </button>
    </div>
  );
}
