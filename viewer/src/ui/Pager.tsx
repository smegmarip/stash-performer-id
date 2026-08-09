export function Pager({
  total,
  offset,
  page,
  busy,
  onOffset,
}: {
  total: number;
  offset: number;
  page: number;
  busy: boolean;
  onOffset: (offset: number) => void;
}) {
  if (total <= page) return null;
  return (
    <div className="pager">
      <button disabled={busy || offset === 0} onClick={() => onOffset(Math.max(0, offset - page))}>
        Prev
      </button>
      <span>
        {offset + 1}–{Math.min(offset + page, total)} of {total}
      </span>
      <button disabled={busy || offset + page >= total} onClick={() => onOffset(offset + page)}>
        Next
      </button>
    </div>
  );
}
