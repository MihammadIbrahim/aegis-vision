// WCAG 2.1 contrast check of every foreground/background token pair the app uses, in both themes.
// Text needs 4.5:1 (AA); non-text UI parts (control borders, focus rings, outline-button borders) 3:1.
// Disabled controls are exempt in WCAG, but we still hold their text to 4.5:1.
import { describe, expect, it } from "vitest";

import tokensCss from "../styles/tokens.css?raw";

function block(selector: string): Record<string, string> {
  const start = tokensCss.indexOf(`${selector} {`);
  const body = tokensCss.slice(start, tokensCss.indexOf("\n}", start));
  const values: Record<string, string> = {};
  for (const match of body.matchAll(/(--[\w-]+):\s*(#[0-9a-fA-F]{6})\b/g)) values[match[1]] = match[2].toLowerCase();
  return values;
}

const light = block(":root");
const dark = { ...light, ...block('[data-theme="dark"]') };

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const TEXT = 4.5;
const UI = 3;
const surfaces = ["--color-bg", "--color-surface", "--color-surface-elevated", "--color-surface-hover", "--color-surface-header"];

const PAIRS: Array<[string, string, number, string]> = [
  ...surfaces.flatMap((bg) => [
    ["--color-text", bg, TEXT, "body text"] as [string, string, number, string],
    ["--color-text-secondary", bg, TEXT, "secondary text"] as [string, string, number, string],
    ["--color-text-muted", bg, TEXT, "muted text"] as [string, string, number, string],
    ["--color-link", bg, TEXT, "links"] as [string, string, number, string],
    ["--color-danger", bg, TEXT, "danger text / links"] as [string, string, number, string],
    ["--color-focus", bg, UI, "focus ring"] as [string, string, number, string],
  ]),
  ["--color-on-primary", "--color-primary", TEXT, "primary button"],
  ["--color-on-primary", "--color-primary-hover", TEXT, "primary button hover"],
  ["--color-primary", "--color-surface", TEXT, "secondary (outline) button text"],
  ["--color-primary", "--color-surface-hover", TEXT, "secondary button hover"],
  ["--color-primary", "--color-surface", UI, "outline button border"],
  ["--color-on-accent", "--color-accent", TEXT, "accent button"],
  ["--color-on-danger", "--color-danger-solid", TEXT, "danger button"],
  ["--color-on-danger", "--color-danger-solid-hover", TEXT, "danger button hover"],
  ["--color-disabled-text", "--color-disabled-bg", TEXT, "disabled control"],
  ["--color-text", "--color-input-bg", TEXT, "input text"],
  ["--color-text-muted", "--color-input-bg", TEXT, "placeholder"],
  ["--color-border-strong", "--color-input-bg", UI, "input border"],
  ["--color-border-strong", "--color-surface", UI, "input border on cards"],
  ["--color-border-strong", "--color-bg", UI, "input border on the page"],
  ["--color-focus", "--color-input-bg", UI, "focus ring on inputs"],
  ["--color-sidebar-text", "--color-sidebar-bg", TEXT, "sidebar links"],
  ["--color-sidebar-brand", "--color-sidebar-bg", TEXT, "sidebar product name"],
  ["--color-sidebar-active-text", "--color-sidebar-active-bg", TEXT, "active sidebar link"],
  ["--color-sidebar-focus", "--color-sidebar-bg", UI, "focus ring in the sidebar"],
  ["--color-topbar-text", "--color-topbar-bg", TEXT, "topbar text (gradient start)"],
  ["--color-topbar-text", "--color-topbar-end", TEXT, "topbar text (gradient end)"],
  ["--color-topbar-link", "--color-topbar-bg", TEXT, "topbar link (start)"],
  ["--color-topbar-link", "--color-topbar-end", TEXT, "topbar link (end)"],
  ["--color-topbar-control-text", "--color-topbar-control-bg", TEXT, "topbar button"],
  ["--color-on-accent", "--color-accent", TEXT, "text on accent"],
  ["--color-success", "--color-surface", TEXT, "success text"],
  ["--color-warning", "--color-surface", TEXT, "warning text"],
  ["--color-info", "--color-surface", TEXT, "info text"],
  ["--color-success-text", "--color-success-bg", TEXT, "success notice"],
  ["--color-warning-text", "--color-warning-bg", TEXT, "warning notice"],
  ["--color-danger-text", "--color-danger-bg", TEXT, "error notice"],
  ["--color-info-text", "--color-info-bg", TEXT, "info notice"],
  ...["normal", "new", "acknowledged", "responding", "resolved", "warning", "unauthorized", "critical", "unknown", "inactive"].map(
    (s) => [`--status-${s}-fg`, `--status-${s}-bg`, TEXT, `status badge: ${s}`] as [string, string, number, string],
  ),
];

export const CONTRAST_REPORT: Array<{ theme: string; pair: string; ratio: number; min: number }> = [];

describe.each([
  ["light", light],
  ["dark", dark],
] as const)("%s theme contrast", (theme, values) => {
  it("defines every token used in the checks", () => {
    for (const [fg, bg] of PAIRS) {
      expect(values[fg], fg).toMatch(/^#[0-9a-f]{6}$/);
      expect(values[bg], bg).toMatch(/^#[0-9a-f]{6}$/);
    }
  });

  it.each(PAIRS)("%s on %s ≥ %s:1 (%s)", (fg, bg, min) => {
    const ratio = contrast(values[fg], values[bg]);
    CONTRAST_REPORT.push({ theme, pair: `${fg} on ${bg}`, ratio: Math.round(ratio * 100) / 100, min });
    expect(ratio, `${fg} ${values[fg]} on ${bg} ${values[bg]} = ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(min);
  });
});
