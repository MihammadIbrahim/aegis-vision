import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "./Button";

/** Previous / Next over a list. With ``total`` it shows "first–last of total". Some lists have no
 * total: then pass ``count`` (rows on this page) and ``hasMore`` instead. */
export function Pagination({
  total,
  count,
  hasMore,
  limit,
  offset,
  onChange,
}: {
  total?: number;
  count?: number;
  hasMore?: boolean;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}) {
  const known = total !== undefined;
  const shown = known ? Math.min(limit, Math.max(0, total - offset)) : (count ?? 0);
  const first = shown === 0 ? 0 : offset + 1;
  const last = offset + shown;
  const more = known ? offset + limit < total : Boolean(hasMore);
  return (
    <nav className="pager" aria-label="Pagination">
      <span>
        {first}–{last}
        {known ? ` of ${total}` : ""}
      </span>
      <Button variant="secondary" size="sm" icon={ChevronLeft} disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        Previous
      </Button>
      <Button variant="secondary" size="sm" icon={ChevronRight} disabled={!more} onClick={() => onChange(offset + limit)}>
        Next
      </Button>
    </nav>
  );
}
