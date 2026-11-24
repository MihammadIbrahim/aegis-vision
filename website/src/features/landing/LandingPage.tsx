import { ArrowRight, ChevronRight, Menu, Moon, ShieldCheck, Sun, UserPlus, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { buttonClass, StatusBadge } from "../../components/ui";
import { useTheme } from "../../lib/theme";
import { CAPABILITY_GROUPS, HIERARCHY, PIPELINE, ROLES, SECURITY, STACK } from "./content";
import "./landing.css";

const SECTIONS: Array<[string, string]> = [
  ["capabilities", "Capabilities"],
  ["how-it-works", "How it works"],
  ["roles", "Security & roles"],
  ["architecture", "Architecture"],
];

/** Page width and side padding shared by every section. */
const CONTAINER = "mx-auto w-full max-w-7xl px-4 sm:px-6 lg:px-8";
const EYEBROW = "m-0 font-mono text-xs uppercase tracking-[0.14em] text-fg-muted";
const H2 = "mt-3 mb-0 text-3xl font-semibold leading-tight tracking-tight text-fg sm:text-4xl";
const CARD = "flex h-full flex-col rounded-2xl border border-border bg-surface p-6 shadow-card transition-[box-shadow,border-color] duration-200 hover:border-border-strong hover:shadow-lift";
const ICON_TILE = "grid size-10 place-items-center rounded-xl text-primary";
const ICON_TINT = { background: "color-mix(in srgb, var(--color-primary) 12%, transparent)" };

function SectionHead({ eyebrow, title, id, children }: { eyebrow: string; title: string; id: string; children?: ReactNode }) {
  return (
    <header className="max-w-3xl">
      <p className={EYEBROW}>{eyebrow}</p>
      <h2 id={id} className={H2}>
        {title}
      </h2>
      {children}
    </header>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useTheme();
  const dark = theme === "dark";
  return (
    <button
      type="button"
      className={buttonClass("ghost", "sm", "min-w-9")}
      aria-pressed={dark}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      onClick={() => setTheme(dark ? "light" : "dark")}
    >
      {dark ? <Sun className="icon" aria-hidden="true" /> : <Moon className="icon" aria-hidden="true" />}
      <span className="hidden xl:inline">{dark ? "Light" : "Dark"}</span>
    </button>
  );
}

/** Illustration of the operations console: a campus map, an alert and where it is routed. Sample data,
 * drawn with SVG and the theme's colours (no images). */
function ConsolePreview() {
  return (
    <figure className="m-0 overflow-hidden rounded-2xl border border-border bg-surface shadow-lift" aria-labelledby="console-caption">
      <div className="flex items-center gap-3 border-b border-border bg-surface-header px-4 py-3">
        <span className="inline-flex gap-1.5" aria-hidden="true">
          <i className="size-2.5 rounded-full bg-border-strong" />
          <i className="size-2.5 rounded-full bg-border-strong" />
          <i className="size-2.5 rounded-full bg-border-strong" />
        </span>
        <span className="mono truncate text-fg-muted">campus / main — operations</span>
      </div>
      <div className="grid gap-4 p-4 sm:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)] sm:items-start">
        <svg className="lp-map" viewBox="0 0 320 220" role="img" aria-labelledby="map-title" aria-describedby="map-desc">
          <title id="map-title">Campus map</title>
          <desc id="map-desc">
            Three locations: Admin Block, Library and the north perimeter. The gate camera of the Admin Block raises an alert,
            which is routed to the two Guards stationed there; a Standby Guard can be reached by escalation.
          </desc>
          <defs>
            <pattern id="lp-grid" width="20" height="20" patternUnits="userSpaceOnUse">
              <path d="M 20 0 L 0 0 0 20" className="lp-grid-line" />
            </pattern>
          </defs>
          <rect x="0" y="0" width="320" height="220" fill="url(#lp-grid)" />
          <rect x="12" y="14" width="296" height="22" className="lp-zone lp-zone-outdoor" rx="3" />
          <text x="20" y="29" className="lp-label">North perimeter</text>
          <rect x="20" y="52" width="140" height="110" className="lp-zone lp-zone-alert" rx="4" />
          <text x="30" y="70" className="lp-label">Admin Block</text>
          <text x="30" y="84" className="lp-sub">Ground · Main gate</text>
          <rect x="180" y="52" width="120" height="80" className="lp-zone" rx="4" />
          <text x="190" y="70" className="lp-label">Library</text>
          <text x="190" y="84" className="lp-sub">Floor 1 · Reading hall</text>
          <circle cx="60" cy="130" r="14" className="lp-pulse" />
          <circle cx="60" cy="130" r="5" className="lp-cam lp-cam-alert" />
          <circle cx="250" cy="110" r="5" className="lp-cam" />
          <circle cx="240" cy="25" r="4" className="lp-cam" />
          <path d="M 60 130 L 118 120" className="lp-route" />
          <path d="M 60 130 L 118 148" className="lp-route" />
          <path d="M 60 130 C 120 200 220 200 268 178" className="lp-route lp-route-dashed" />
          <rect x="114" y="113" width="14" height="14" rx="3" className="lp-guard" />
          <rect x="114" y="141" width="14" height="14" rx="3" className="lp-guard" />
          <rect x="262" y="171" width="14" height="14" rx="3" className="lp-guard lp-guard-standby" />
          <text x="226" y="200" className="lp-sub">Standby pool</text>
        </svg>
        <div className="rounded-xl border border-border border-l-4 border-l-danger bg-surface p-4 shadow-card" aria-hidden="true">
          <div className="flex items-center justify-between gap-2">
            <span className="rounded px-2 py-0.5 font-mono text-xs font-semibold" style={{ background: "var(--status-critical-bg)", color: "var(--status-critical-fg)" }}>
              HIGH
            </span>
            <span className="mono text-xs text-fg-muted">ALERT · NEW</span>
          </div>
          <p className="mt-3 mb-1 font-semibold leading-snug">Possible weapon · Gate Camera</p>
          <p className="m-0 font-mono text-xs text-fg-muted">Admin Block › Ground › Main gate</p>
          <ul className="mt-3 mb-0 list-none p-0 text-sm">
            {[
              ["Stationed Guards", "2 notified"],
              ["Escalation", "Standby pool"],
              ["Status", "For human review"],
            ].map(([k, v]) => (
              <li key={k} className="flex justify-between gap-2 border-t border-border py-1.5">
                <span className="text-fg-muted">{k}</span>
                <strong className="text-right font-medium">{v}</strong>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <figcaption id="console-caption" className="border-t border-border px-4 py-3 text-xs text-fg-muted">
        Illustration with sample data. Weapon detection itself is planned; routing and response are built.
      </figcaption>
    </figure>
  );
}

/** The public landing page for visitors who are not signed in. */
export function LandingPage() {
  const [menu, setMenu] = useState(false);

  useEffect(() => {
    if (!menu) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenu(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menu]);

  const linkClass = "rounded-md px-3 py-2 text-sm font-medium text-fg-secondary no-underline transition-colors hover:bg-surface-hover hover:text-fg";

  return (
    <div className="lp-root min-h-screen bg-bg text-fg">
      <a className="skip-link" href="#lp-main">
        Skip to content
      </a>
      <header className="sticky top-0 z-30 border-b border-border bg-bg/85 backdrop-blur-md">
        <div className={`${CONTAINER} flex h-16 items-center gap-4`}>
          <Link to="/" className="inline-flex shrink-0 items-center gap-2.5 font-semibold text-fg no-underline" aria-label="AI CCTV Security, home">
            <span className="grid size-8 place-items-center rounded-lg bg-primary text-on-primary shadow-sm">
              <ShieldCheck className="icon" aria-hidden="true" />
            </span>
            <span>AI CCTV Security</span>
          </Link>
          <nav
            aria-label="Sections"
            id="lp-sections"
            className={[
              "items-center gap-1 lg:ml-4 lg:flex",
              menu
                ? "absolute inset-x-0 top-16 flex flex-col items-stretch border-b border-border bg-bg p-3 shadow-lift lg:static lg:flex-row lg:border-0 lg:p-0 lg:shadow-none"
                : "hidden",
            ].join(" ")}
          >
            {SECTIONS.map(([id, label]) => (
              <a key={id} href={`#${id}`} className={linkClass} onClick={() => setMenu(false)}>
                {label}
              </a>
            ))}
            <Link className={`${linkClass} sm:hidden`} to="/login">
              Sign in
            </Link>
            <Link className={`${linkClass} sm:hidden`} to="/register">
              Create account
            </Link>
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <ThemeToggle />
            <span className="mx-1 hidden h-6 w-px bg-border sm:block" aria-hidden="true" />
            <Link className={buttonClass("ghost", "sm", "hidden sm:inline-flex")} to="/login">
              Sign in
            </Link>
            <Link className={buttonClass("primary", "sm", "hidden sm:inline-flex")} to="/register">
              Create account
            </Link>
            <button
              type="button"
              className={buttonClass("ghost", "sm", "min-w-9 lg:hidden")}
              aria-label={menu ? "Close menu" : "Open menu"}
              aria-expanded={menu}
              aria-controls="lp-sections"
              onClick={() => setMenu(!menu)}
            >
              {menu ? <X className="icon" aria-hidden="true" /> : <Menu className="icon" aria-hidden="true" />}
            </button>
          </div>
        </div>
      </header>

      <main id="lp-main">
        <section className="relative overflow-hidden border-b border-border bg-canvas" aria-labelledby="hero-title">
          <div className="lp-gridbg pointer-events-none absolute inset-0" aria-hidden="true" />
          <div className={`${CONTAINER} relative grid items-center gap-12 py-16 sm:py-20 lg:grid-cols-12 lg:py-24`}>
            <div className="lg:col-span-6">
              <p className="m-0 inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1 font-mono text-xs uppercase tracking-[0.12em] text-fg-secondary shadow-card">
                <span className="size-1.5 rounded-full bg-success" aria-hidden="true" />
                Organizational security operations
              </p>
              <h1 id="hero-title" className="mt-6 mb-0 text-4xl leading-[1.08] font-semibold tracking-tight text-fg sm:text-5xl lg:text-[3.4rem]">
                Cameras, alerts and security staff, coordinated across every campus.
              </h1>
              <p className="mt-6 mb-0 max-w-xl text-lg leading-relaxed text-fg-secondary">
                A security management platform for universities, companies, offices, warehouses and industrial sites. It maps
                each campus, turns camera detections into located alerts, routes them to the right Guards and keeps a full
                record, with AI as a signal for trained staff, never as proof.
              </p>
              <div className="mt-8 flex flex-wrap gap-3">
                <Link className={buttonClass("primary", "lg", "max-sm:w-full")} to="/register">
                  <UserPlus className="icon" aria-hidden="true" /> Create account
                </Link>
                <Link className={buttonClass("outline", "lg", "max-sm:w-full")} to="/login">
                  Sign in <ArrowRight className="icon" aria-hidden="true" />
                </Link>
              </div>
              <p className="mt-4 mb-0 text-sm text-fg-muted">For organizations only; not for private homes.</p>
            </div>
            <div className="min-w-0 lg:col-span-6">
              <ConsolePreview />
            </div>
          </div>
        </section>

        <section className="border-b border-border bg-bg" aria-label="Principle">
          <div className={`${CONTAINER} grid gap-3 py-8 md:grid-cols-12 md:items-baseline md:gap-8`}>
            <span className={`${EYEBROW} md:col-span-2`}>Principle</span>
            <p className="m-0 text-md leading-relaxed text-fg-secondary md:col-span-10">
              <strong className="text-fg">AI outputs are decision-support signals, not proof.</strong> Uncertain detections and identity
              matches are reviewed by an authorized person; the system never declares anyone guilty, and never presents an
              uncertain identity as fact.
            </p>
          </div>
        </section>

        <section id="capabilities" className="bg-bg py-20 sm:py-24" aria-labelledby="cap-title">
          <div className={CONTAINER}>
            <SectionHead eyebrow="01 · Capabilities" title="What the platform does, and how far each part is built" id="cap-title">
              <p className="mt-5 mb-0 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-fg-muted">
                <span className="inline-flex items-center gap-2">
                  <StatusBadge status="AVAILABLE" /> built and tested
                </span>
                <span className="inline-flex items-center gap-2">
                  <StatusBadge status="PARTIAL" /> partly built
                </span>
                <span className="inline-flex items-center gap-2">
                  <StatusBadge status="PLANNED" /> in the requirements, not built yet
                </span>
              </p>
            </SectionHead>
            <div className="mt-12 flex flex-col gap-12">
              {CAPABILITY_GROUPS.map((group) => (
                <div key={group.id} className="grid gap-6 border-t border-border pt-10 lg:grid-cols-12 lg:gap-8">
                  <div className="lg:col-span-4">
                    <h3 className="m-0 text-xl font-semibold tracking-tight">{group.title}</h3>
                    <p className="mt-2 mb-0 text-fg-muted">{group.lead}</p>
                  </div>
                  <ul className="m-0 grid list-none gap-4 p-0 sm:grid-cols-2 lg:col-span-8">
                    {group.items.map((c) => (
                      <li key={c.title} className={CARD}>
                        <div className="flex items-start justify-between gap-3">
                          <span className={ICON_TILE} style={ICON_TINT}>
                            <c.icon className="size-5" aria-hidden="true" />
                          </span>
                          <StatusBadge status={c.status} />
                        </div>
                        <h4 className="mt-5 mb-2 text-md font-semibold leading-snug text-fg">{c.title}</h4>
                        <p className={`mt-0 text-fg-secondary ${c.note ? "mb-4" : "mb-0"}`}>{c.text}</p>
                        {c.note && (
                          <p className="mt-auto mb-0 border-t border-border pt-3 text-sm text-fg-muted">
                            {c.note}
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section id="how-it-works" className="border-y border-border bg-canvas py-20 sm:py-24" aria-labelledby="how-title">
          <div className={CONTAINER}>
            <SectionHead eyebrow="02 · How it works" title="From a camera to a Guard on site" id="how-title" />
            <ol className="mt-10 mb-0 flex list-none flex-wrap items-center gap-2 p-0" aria-label="Location hierarchy">
              {HIERARCHY.map((level, i) => (
                <li key={level} className="inline-flex items-center gap-2">
                  <span className="inline-flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm font-medium shadow-card">
                    <span className="font-mono text-xs text-fg-muted">{String(i + 1).padStart(2, "0")}</span> {level}
                  </span>
                  {i < HIERARCHY.length - 1 && <ChevronRight className="icon text-fg-muted" aria-hidden="true" />}
                </li>
              ))}
            </ol>
            <ol className="mt-8 mb-0 grid list-none gap-4 p-0 md:grid-cols-2 lg:grid-cols-5">
              {PIPELINE.map((s) => (
                <li key={s.step} className={`${CARD} md:last:col-span-2 lg:last:col-span-1`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="grid size-9 place-items-center rounded-full bg-primary font-mono text-sm font-medium text-on-primary">
                      {s.step}
                    </span>
                    <StatusBadge status={s.status} />
                  </div>
                  <h3 className="mt-5 mb-2 text-md font-semibold">{s.title}</h3>
                  <p className="m-0 text-sm text-fg-secondary">{s.text}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id="roles" className="bg-bg py-20 sm:py-24" aria-labelledby="roles-title">
          <div className={CONTAINER}>
            <SectionHead eyebrow="03 · Security & roles" title="Each role sees its own campus, and only what it needs" id="roles-title" />
            <ul className="mt-12 mb-0 grid list-none gap-4 p-0 md:grid-cols-3">
              {ROLES.map((r) => (
                <li key={r.title} className={CARD}>
                  <div className="flex items-start justify-between gap-3">
                    <span className={ICON_TILE} style={ICON_TINT}>
                      <r.icon className="size-5" aria-hidden="true" />
                    </span>
                    <StatusBadge status={r.status} label={r.status === "PARTIAL" ? "App planned" : undefined} />
                  </div>
                  <h3 className="mt-5 mb-1 text-lg font-semibold">{r.title}</h3>
                  <p className="m-0 font-mono text-xs uppercase tracking-[0.1em] text-fg-muted">{r.where}</p>
                  <ul className="mt-4 mb-0 flex list-none flex-col gap-2.5 p-0 text-fg-secondary">
                    {r.points.map((p) => (
                      <li key={p} className="flex gap-2.5">
                        <span className="mt-2 size-1.5 shrink-0 rounded-full bg-primary" aria-hidden="true" />
                        <span>{p}</span>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
            <p className="mt-6 mb-0 max-w-3xl border-l-2 border-border-strong pl-4 text-sm text-fg-muted">
              Guard sign-in, alerts, responses and reports already work through the API; the Guard mobile app itself is planned.
              A separate Platform Super Admin oversees organizations without access to their footage or records.
            </p>
            <h3 className="mt-16 mb-6 text-xl font-semibold tracking-tight">Security controls</h3>
            <ul className="m-0 grid list-none gap-4 p-0 sm:grid-cols-2 lg:grid-cols-3">
              {SECURITY.map((s) => (
                <li key={s.title} className="flex h-full flex-col rounded-2xl border border-border bg-surface-header p-5">
                  <div className="flex items-start justify-between gap-3">
                    <h4 className="m-0 text-md font-semibold leading-snug">{s.title}</h4>
                    <StatusBadge status={s.status} />
                  </div>
                  <p className="mt-2 mb-0 text-sm text-fg-secondary">{s.text}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section id="architecture" className="border-y border-border bg-canvas py-20 sm:py-24" aria-labelledby="arch-title">
          <div className={`${CONTAINER} grid gap-10 lg:grid-cols-12 lg:items-start`}>
            <div className="lg:col-span-5">
              <SectionHead eyebrow="04 · Architecture" title="Separate services with clear boundaries" id="arch-title">
                <p className="mt-4 mb-0 text-fg-muted">
                  Video analysis runs apart from the API so it never blocks normal requests. Business rules sit in a Clean
                  Architecture core, and every service talks through documented contracts.
                </p>
              </SectionHead>
            </div>
            <div className="min-w-0 lg:col-span-7">
              <div className="table-scroll rounded-2xl">
                <table className="text-sm">
                  <caption className="sr-only">Technology stack and status</caption>
                  <thead>
                    <tr>
                      <th scope="col" className="px-5">Layer</th>
                      <th scope="col">Technology</th>
                      <th scope="col">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {STACK.map((row) => (
                      <tr key={row.layer}>
                        <th scope="row" className="bg-transparent px-5 py-3.5 font-semibold whitespace-nowrap text-fg">
                          {row.layer}
                        </th>
                        <td className="py-3.5 text-fg-secondary">{row.tech}</td>
                        <td className="py-3.5">
                          <StatusBadge status={row.status} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </section>

        <section className="bg-bg py-20 sm:py-24" aria-labelledby="band-title">
          <div className={CONTAINER}>
            <div
              className="brand-panel relative overflow-hidden rounded-3xl px-6 py-14 text-center shadow-lift sm:px-12"
            >
              <h2 id="band-title" className="m-0 text-3xl font-semibold tracking-tight text-topbar-text sm:text-4xl">
                Set up your organization
              </h2>
              <p className="mx-auto mt-4 mb-0 max-w-2xl text-md">
                Register, verify your email and sign in as the Main Admin. Then map your campus, add cameras and invite your team.
              </p>
              <div className="mt-8 flex flex-wrap justify-center gap-3">
                <Link className="button btn btn-lg max-sm:w-full border-topbar-control-border bg-topbar-control-bg text-topbar-control-text hover:bg-topbar-control-bg hover:opacity-90" to="/register">
                  <UserPlus className="icon" aria-hidden="true" /> Create account
                </Link>
                <Link className="button btn btn-lg max-sm:w-full border-topbar-text bg-transparent text-topbar-text shadow-none hover:bg-white/10 hover:border-topbar-text" to="/login">
                  Sign in
                </Link>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-border bg-canvas">
        <div className={`${CONTAINER} grid gap-8 py-12 md:grid-cols-12`}>
          <div className="md:col-span-6">
            <p className="m-0 inline-flex items-center gap-2.5 font-semibold">
              <span className="grid size-8 place-items-center rounded-lg bg-primary text-on-primary">
                <ShieldCheck className="icon" aria-hidden="true" />
              </span>
              AI CCTV Security
            </p>
            <p className="mt-3 mb-0 max-w-sm text-sm text-fg-muted">AI-powered CCTV surveillance and security management for organizations.</p>
          </div>
          <nav aria-label="Footer" className="md:col-span-3">
            <p className={`${EYEBROW} mb-3`}>Access</p>
            <ul className="m-0 flex list-none flex-col gap-2 p-0 text-sm">
              <li>
                <Link to="/login">Sign in</Link>
              </li>
              <li>
                <Link to="/register">Create account</Link>
              </li>
              <li>
                <a href="#capabilities">Capabilities</a>
              </li>
            </ul>
          </nav>
          <div className="md:col-span-3">
            <p className={`${EYEBROW} mb-3`}>Stack</p>
            <p className="m-0 text-sm text-fg-muted">React · FastAPI · PostgreSQL 16 (RLS) · ONNX Runtime</p>
          </div>
          <p className="m-0 border-t border-border pt-6 text-xs text-fg-muted md:col-span-12">
            © 2026 AI CCTV Organizational Security Management System · Final Year Project
          </p>
        </div>
      </footer>
    </div>
  );
}
