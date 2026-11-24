import type { Key, ReactNode } from "react";

import { EmptyState, LoadingState } from "./States";

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  align?: "left" | "right";
  className?: string;
}

/** A plain, accessible table: a caption for screen readers, horizontal scrolling on narrow
 * screens, and loading / empty rows. Actions belong in a column as real buttons or links. */
export function DataTable<T>({
  caption,
  columns,
  rows,
  rowKey,
  loading = false,
  empty = "Nothing to show.",
}: {
  caption: string;
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => Key;
  loading?: boolean;
  empty?: string;
}) {
  return (
    <div className="table-scroll">
      <table>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={c.align === "right" ? "num" : undefined}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <tr>
              <td colSpan={columns.length}>
                <LoadingState />
              </td>
            </tr>
          ) : rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length}>
                <EmptyState title={empty} />
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr key={rowKey(row)}>
                {columns.map((c) => (
                  <td key={c.key} className={[c.align === "right" ? "num" : "", c.className ?? ""].join(" ").trim() || undefined}>
                    {c.render(row)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
