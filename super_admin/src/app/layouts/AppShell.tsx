import { LifeBuoy, LogOut, Menu, Moon, PanelLeftClose, PanelLeftOpen, Sun, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";

import { useAuth } from "../../auth/AuthContext";
import { useTheme } from "../../lib/theme";
import { NAV_GROUPS } from "../nav";

const COLLAPSED_KEY = "cctv.platform.sidebarCollapsed";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

/** The signed-in layout of the Super Admin app: grouped sidebar, topbar and main content.
 * ≥1024 px: the sidebar is fixed and can be collapsed to icons (remembered).
 * <1024 px: the sidebar is an overlay opened from the menu button; Escape, the backdrop or
 * choosing a page closes it. */
export function AppShell({ children }: { children: ReactNode }) {
  const { session, signOut } = useAuth();
  const groups = NAV_GROUPS;
  const [theme, setTheme] = useTheme();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  useEffect(() => setMobileOpen(false), [location.pathname, location.search]);
  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMobileOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileOpen]);

  function toggleCollapsed() {
    const next = !collapsed;
    setCollapsed(next);
    try {
      localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0");
    } catch {
      // not stored; fine
    }
  }

  return (
    <div className={`shell${collapsed ? " is-collapsed" : ""}${mobileOpen ? " is-menu-open" : ""}`}>
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <aside className="sidebar" id="app-sidebar" aria-label="Sidebar">
        <div className="sidebar-head">
          <span className="brand">
            <span className="brand-full">AI CCTV · Platform</span>
            <span className="brand-short" aria-hidden="true">
              AI
            </span>
          </span>
          <button type="button" className="btn btn-ghost btn-sm sidebar-close" aria-label="Close menu" onClick={() => setMobileOpen(false)}>
            <X className="icon" aria-hidden="true" />
          </button>
        </div>
        <nav aria-label="Main">
          {groups.map((group) => (
            <div className="nav-group" key={group.label}>
              <p className="nav-group-label">{group.label}</p>
              <ul>
                {group.items.map((item) => (
                  <li key={item.to}>
                    <NavLink to={item.to} end title={collapsed ? item.label : undefined}>
                      <item.icon className="icon" aria-hidden="true" />
                      <span className="nav-label">{item.label}</span>
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
      </aside>
      <div className="sidebar-backdrop" aria-hidden="true" onClick={() => setMobileOpen(false)} />
      <div className="main">
        <header className="topbar">
          <button
            type="button"
            className="btn btn-ghost btn-sm topbar-icon menu-button"
            aria-label="Open menu"
            aria-controls="app-sidebar"
            aria-expanded={mobileOpen}
            onClick={() => setMobileOpen(true)}
          >
            <Menu className="icon" aria-hidden="true" />
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm topbar-icon collapse-button"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-pressed={collapsed}
            onClick={toggleCollapsed}
          >
            {collapsed ? <PanelLeftOpen className="icon" aria-hidden="true" /> : <PanelLeftClose className="icon" aria-hidden="true" />}
          </button>
          <span className="topbar-spacer" />
          <span className="topbar-user">
            <span className="topbar-name">{session?.admin.username}</span>
            <span className="topbar-role">{session?.loginMethod === "RECOVERY" ? "Emergency session" : "Platform Super Admin"}</span>
          </span>
          <button
            type="button"
            className="secondary theme-toggle"
            aria-pressed={theme === "dark"}
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            {theme === "dark" ? <Sun className="icon" aria-hidden="true" /> : <Moon className="icon" aria-hidden="true" />}
            <span className="theme-label">{theme === "dark" ? "Light theme" : "Dark theme"}</span>
          </button>
          <button type="button" className="link signout" onClick={signOut}>
            <LogOut className="icon" aria-hidden="true" />
            Sign out
          </button>
        </header>
        <main className="content" id="main-content" tabIndex={-1}>
          {session?.loginMethod === "RECOVERY" && (
            <div className="notice warning recovery-banner" role="status">
              <LifeBuoy className="icon" aria-hidden="true" />
              <span>
                You signed in with the emergency recovery credentials.{" "}
                <Link to="/profile#recovery">Restore the normal username and password</Link> now.
              </span>
            </div>
          )}
          {children}
        </main>
      </div>
    </div>
  );
}
