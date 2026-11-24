import type { ReactNode } from "react";

export interface Bar {
  key: string;
  label: ReactNode;
  /** Plain-text name, for the tooltip. */
  name: string;
  value: number;
}

/** A single-series horizontal bar list: each row names its category in text and prints its value,
 * so nothing depends on colour, and the list is itself the table view. Bars share one scale
 * starting at zero. */
export function BarList({ bars, label, empty }: { bars: Bar[]; label: string; empty: string }) {
  const max = Math.max(0, ...bars.map((b) => b.value));
  if (max === 0) return <p className="muted">{empty}</p>;
  return (
    <ul className="plain bar-list" aria-label={label}>
      {bars.map((b) => (
        <li key={b.key} className="bar-row" title={`${b.name}: ${b.value}`}>
          <span className="bar-label">{b.label}</span>
          <span className="bar-track" aria-hidden="true">
            <span className="bar-fill" style={{ width: `${(b.value / max) * 100}%` }} />
          </span>
          <span className="bar-value">{b.value}</span>
        </li>
      ))}
    </ul>
  );
}
