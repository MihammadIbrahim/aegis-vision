import { useState } from "react";

// Charts follow the project's data-visualisation rules (decision D-024): one axis, thin bars with gaps and
// rounded data ends on the baseline, a recessive grid, categorical colours in a fixed order (validated for
// both themes), a legend whenever there are two or more series, a hover/focus tooltip on every bar, and
// every number also available as a table. Text never takes a series colour.

export interface BarSeries {
  name: string;
  values: number[];
}

const SERIES_VARS = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];

function niceMax(value: number): number {
  if (value <= 4) return 4;
  const step = 10 ** Math.floor(Math.log10(value));
  for (const factor of [1, 2, 2.5, 5, 10]) if (factor * step >= value) return factor * step;
  return 10 * step;
}

/** Vertical bars per category (e.g. per day), grouped by series. */
export function BarChart({
  title,
  categories,
  series,
  height = 180,
  valueLabel = "Count",
}: {
  title: string;
  categories: string[];
  series: BarSeries[];
  height?: number;
  valueLabel?: string;
}) {
  const [hover, setHover] = useState<{ x: number; y: number; text: string } | null>(null);
  const width = 640;
  const pad = { top: 8, right: 8, bottom: 26, left: 32 };
  const max = niceMax(Math.max(0, ...series.flatMap((s) => s.values)));
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const band = plotW / Math.max(1, categories.length);
  const groupGap = Math.min(8, band * 0.25);
  const barW = Math.max(2, (band - groupGap) / Math.max(1, series.length) - 2);
  const y = (v: number) => pad.top + plotH - (v / max) * plotH;
  const ticks = [0, max / 2, max];
  const every = Math.ceil(categories.length / 10);
  return (
    <figure className="chart" aria-label={title} onMouseLeave={() => setHover(null)}>
      <figcaption className="chart-title">{title}</figcaption>
      {series.length > 1 && (
        <ul className="chart-legend" aria-label="Legend">
          {series.map((s, i) => (
            <li key={s.name}>
              <span className="chart-swatch" style={{ background: SERIES_VARS[i % SERIES_VARS.length] }} aria-hidden="true" />
              {s.name}
            </li>
          ))}
        </ul>
      )}
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}: bar chart`}>
        <g className="chart-grid">
          {ticks.map((t) => (
            <line key={t} x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} />
          ))}
        </g>
        <g className="chart-axis">
          {ticks.map((t) => (
            <text key={t} x={pad.left - 6} y={y(t) + 3} textAnchor="end">
              {Number.isInteger(t) ? t : t.toFixed(1)}
            </text>
          ))}
          {categories.map((c, i) =>
            i % every === 0 ? (
              <text key={c} x={pad.left + band * i + band / 2} y={height - 8} textAnchor="middle">
                {c}
              </text>
            ) : null,
          )}
        </g>
        {categories.map((c, i) =>
          series.map((s, j) => {
            const value = s.values[i] ?? 0;
            const x = pad.left + band * i + groupGap / 2 + j * (barW + 2);
            const top = y(value);
            const h = Math.max(0, pad.top + plotH - top);
            const text = `${c} · ${s.name}: ${value}`;
            const show = () => setHover({ x: ((x + barW / 2) / width) * 100, y: (top / height) * 100, text });
            return (
              <rect
                key={`${c}-${s.name}`}
                className="chart-bar"
                x={x}
                y={h > 0 ? top : pad.top + plotH - 1}
                width={barW}
                height={Math.max(1, h)}
                rx={Math.min(4, barW / 2)}
                fill={SERIES_VARS[j % SERIES_VARS.length]}
                tabIndex={0}
                aria-label={text}
                onMouseEnter={show}
                onFocus={show}
                onBlur={() => setHover(null)}
              />
            );
          }),
        )}
      </svg>
      {hover && (
        <div className="chart-tooltip" role="status" style={{ left: `${hover.x}%`, top: `${hover.y}%`, transform: "translate(-50%, -110%)" }}>
          {hover.text}
        </div>
      )}
      <details className="chart-table">
        <summary>Show as table</summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">{valueLabel === "Count" ? "Category" : valueLabel}</th>
                {series.map((s) => (
                  <th key={s.name} scope="col" className="num">
                    {s.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {categories.map((c, i) => (
                <tr key={c}>
                  <th scope="row">{c}</th>
                  {series.map((s) => (
                    <td key={s.name} className="num">
                      {s.values[i] ?? 0}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}

/** One measure per category as labelled horizontal bars (values printed beside each bar). */
export function HorizontalBars({
  title,
  rows,
  format = (v: number) => String(v),
}: {
  title: string;
  rows: { label: string; value: number }[];
  format?: (value: number) => string;
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <figure className="chart" aria-label={title}>
      <figcaption className="chart-title">{title}</figcaption>
      {rows.length === 0 ? (
        <p className="muted small">No data in this period.</p>
      ) : (
        <ul className="hbar-list">
          {rows.map((r) => (
            <li key={r.label} className="hbar-row">
              <span className="truncate" title={r.label}>
                {r.label}
              </span>
              <span className="hbar-track" aria-hidden="true">
                <span className="hbar-fill block" style={{ width: `${(r.value / max) * 100}%` }} />
              </span>
              <span className="hbar-value">{format(r.value)}</span>
            </li>
          ))}
        </ul>
      )}
    </figure>
  );
}
