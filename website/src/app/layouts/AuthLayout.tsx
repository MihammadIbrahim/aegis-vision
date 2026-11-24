import { ArrowLeft, Moon, ShieldCheck, Sun } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { buttonClass } from "../../components/ui";
import { useTheme } from "../../lib/theme";

/** Signed-out pages (sign-in, registration, campus requests): a top bar with the brand, "Back to
 * home" and the theme switch, and one centred card on a quiet full-page grid. ``width`` is the
 * card's maximum width: "md" for sign-in, "xl" for the longer registration forms. */
export function AuthLayout({ children, width = "md" }: { children: ReactNode; width?: "md" | "xl" }) {
  const [theme, setTheme] = useTheme();
  const dark = theme === "dark";
  return (
    <div className="auth-page relative flex min-h-screen flex-col bg-canvas text-fg">
      <div className="auth-backdrop pointer-events-none absolute inset-0" aria-hidden="true" />
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      {/* Not sticky: these pages are short forms, and a fixed bar would cover the form while scrolling. */}
      <header className="relative z-20 border-b border-border bg-bg">
        <div className="mx-auto flex h-16 w-full max-w-7xl items-center gap-3 px-4 sm:px-6">
          <Link className="inline-flex items-center gap-2.5 font-semibold text-fg no-underline" to="/" aria-label="AI CCTV Security, home">
            <span className="grid size-8 place-items-center rounded-lg bg-primary text-on-primary shadow-sm">
              <ShieldCheck className="icon" aria-hidden="true" />
            </span>
            <span className="hidden sm:inline">AI CCTV Security</span>
          </Link>
          <span className="flex-1" />
          <Link className={buttonClass("ghost", "sm")} to="/">
            <ArrowLeft className="icon" aria-hidden="true" />
            Back to home
          </Link>
          <button
            type="button"
            className={buttonClass("outline", "sm", "theme-toggle")}
            aria-pressed={dark}
            onClick={() => setTheme(dark ? "light" : "dark")}
          >
            {dark ? <Sun className="icon" aria-hidden="true" /> : <Moon className="icon" aria-hidden="true" />}
            <span className="max-sm:sr-only">{dark ? "Light theme" : "Dark theme"}</span>
          </button>
        </div>
      </header>
      <main
        id="main-content"
        tabIndex={-1}
        className={`relative mx-auto w-full flex-1 px-4 pt-10 pb-16 outline-none sm:px-6 sm:pt-14 ${width === "xl" ? "max-w-xl" : "max-w-md"}`}
      >
        {children}
      </main>
    </div>
  );
}
