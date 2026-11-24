// Light / dark theme. The first visit follows the operating system; an explicit choice is stored in
// localStorage (not sensitive). index.html applies the stored theme before the first paint.
import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark";
export const THEME_KEY = "cctv.theme";

const listeners = new Set<() => void>();

function systemTheme(): Theme {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function storedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

export function currentTheme(): Theme {
  return storedTheme() ?? systemTheme();
}

export function applyTheme(theme: Theme = currentTheme()): void {
  document.documentElement.dataset.theme = theme;
}

export function setTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Storage unavailable: the choice lasts for this page only.
  }
  applyTheme(theme);
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const media = window.matchMedia?.("(prefers-color-scheme: dark)");
  const onSystemChange = () => {
    if (!storedTheme()) {
      applyTheme();
      listener();
    }
  };
  media?.addEventListener?.("change", onSystemChange);
  return () => {
    listeners.delete(listener);
    media?.removeEventListener?.("change", onSystemChange);
  };
}

export function useTheme(): [Theme, (theme: Theme) => void] {
  const theme = useSyncExternalStore(subscribe, currentTheme, () => "light" as Theme);
  return [theme, setTheme];
}
