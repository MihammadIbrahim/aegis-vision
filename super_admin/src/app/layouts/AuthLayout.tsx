import { Moon, Sun } from "lucide-react";
import type { ReactNode } from "react";

import { useTheme } from "../../lib/theme";

/** Signed-out pages (sign-in): centred content with the theme switch. */
export function AuthLayout({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useTheme();
  return (
    <div className="auth-layout">
      <button
        type="button"
        className="secondary theme-toggle auth-theme"
        aria-pressed={theme === "dark"}
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
      >
        {theme === "dark" ? <Sun className="icon" aria-hidden="true" /> : <Moon className="icon" aria-hidden="true" />}
        {theme === "dark" ? "Light theme" : "Dark theme"}
      </button>
      <main id="main-content">{children}</main>
    </div>
  );
}
