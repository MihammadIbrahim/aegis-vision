import { LogOut, Menu, Moon, PanelLeftClose, PanelLeftOpen, ShieldCheck, Sun, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { useAuth } from "../../auth/AuthContext";
import { navGroupsFor } from "../../auth/permissions";
import { useTheme } from "../../lib/theme";
import { NotificationBell } from "../../features/notifications";
import { CallProvider, useOptionalCalls } from "../../features/calls/CallProvider";
import { CallDock, IncomingCallBanner } from "../../features/calls/components/CallViews";
import { ConnectionStatus } from "../../realtime/ConnectionStatus";
import { RealtimeProvider } from "../../realtime/RealtimeProvider";

const ROLE_NAMES: Record<string, string> = { ADMIN: "Organization Admin", SUPERVISOR: "Supervisor", GUARD: "Guard" };
const COLLAPSED_KEY = "cctv.sidebarCollapsed";

function initials(name: string | undefined): string {
  const parts = (name ?? "").trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() || "?";
}

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

/** The signed-in layout: grouped role-based sidebar, topbar and main content.
 * ≥1024 px: the sidebar is fixed and can be collapsed to icons (remembered).
 * <1024 px: the sidebar is an overlay opened from the menu button; Escape, the backdrop or
 * choosing a page closes it. */
export function AppShell({ children }: { children: ReactNode }) {
  // Live updates (WebSocket signals) and in-app voice calls live as long as the signed-in shell (decision D-026).
  return (
    <RealtimeProvider>
      <CallProvider>
        <ShellLayout>{children}</ShellLayout>
        <IncomingCallBanner />
        <CallDock />
      </CallProvider>
    </RealtimeProvider>
  );
}

function ShellLayout({ children }: { children: ReactNode }) {
  const { session, signOut } = useAuth();
  const groups = navGroupsFor(session?.role, session?.is_main_admin ?? false);
  const [theme, setTheme] = useTheme();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  // While the call dock (or a call notice) is shown, the page gets room to scroll clear of it (decision D-026).
  const calls = useOptionalCalls();
  const docked = Boolean(calls?.current || calls?.notice);
  // The topbar names the current page: the navigation item whose path is the longest match.
  const here = groups
    .flatMap((group) => group.items.map((item) => ({ group: group.label, item })))
    .filter(({ item }) => location.pathname === item.to || location.pathname.startsWith(`${item.to}/`))
    .sort((a, b) => b.item.to.length - a.item.to.length)[0];
  const roleName = ROLE_NAMES[session?.role ?? ""] ?? session?.role;

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
            <span className="brand-mark" aria-hidden="true">
              <ShieldCheck className="icon" />
            </span>
            <span className="brand-full">AI CCTV Security</span>
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
          {here && (
            <span className="topbar-title" aria-hidden="true">
              <span className="topbar-crumb">{here.group}</span>
              <span className="topbar-page">{here.item.label}</span>
            </span>
          )}
          <span className="topbar-spacer" />
          <ConnectionStatus />
          <NotificationBell />
          <span className="topbar-divider" aria-hidden="true" />
          <span className="topbar-chip">
            <span className="avatar" aria-hidden="true">
              {initials(session?.full_name)}
            </span>
            <span className="topbar-user">
              <span className="topbar-name">{session?.full_name}</span>
              <span className="topbar-role">{roleName}</span>
            </span>
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
          <button type="button" className="link signout" onClick={() => signOut()}>
            <LogOut className="icon" aria-hidden="true" />
            <span className="signout-label">Sign out</span>
          </button>
        </header>
        <main className={`content${docked ? " has-call-dock" : ""}`} id="main-content" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}
