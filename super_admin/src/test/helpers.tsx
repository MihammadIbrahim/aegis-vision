import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import type { LoginMethod } from "../api/types";
import { StaticAuthProvider, type Session } from "../auth/AuthContext";
import { ToastProvider } from "../components/ui";

export function sessionFor(loginMethod: LoginMethod = "NORMAL"): Session {
  return {
    admin: { id: "A1", username: "platform.root", role: "PLATFORM_SUPER_ADMIN", created_at: "2026-10-27T08:00:00Z", last_login_at: "2026-11-12T08:00:00Z" },
    loginMethod,
  };
}

/** Renders ``ui`` for a signed-in Super Admin. With ``route`` (e.g. "/organizations/:organizationId")
 * the page gets its URL params. ``onToken`` sees tokens the page stores. */
export function renderAs(loginMethod: LoginMethod, ui: ReactElement, path = "/", route?: string, onToken?: (t: string) => void) {
  const content = route ? (
    <Routes>
      <Route path={route} element={ui} />
      <Route path="*" element={<div>navigated away</div>} />
    </Routes>
  ) : (
    ui
  );
  return render(
    <MemoryRouter initialEntries={[path]}>
      <StaticAuthProvider session={sessionFor(loginMethod)} onToken={onToken}>
        <ToastProvider>{content}</ToastProvider>
      </StaticAuthProvider>
    </MemoryRouter>,
  );
}

export interface Call {
  method: string;
  url: string;
  body: unknown;
}

type RouteSpec = [RegExp, unknown, number?] | { method: string; url: RegExp; body: unknown; status?: number };

/** Replaces fetch: each call is answered by the first route matching the URL (and method, when given).
 * Returns the recorded calls, with JSON bodies parsed and FormData kept as is. */
export function mockApi(routes: RouteSpec[]): Call[] {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    const raw = init?.body;
    calls.push({ method, url, body: typeof raw === "string" ? JSON.parse(raw) : raw });
    const route = routes.find((r) =>
      Array.isArray(r) ? r[0].test(url) : r.method.toUpperCase() === method && r.url.test(url),
    );
    if (!route) return new Response(JSON.stringify({ detail: `not mocked: ${method} ${url}` }), { status: 500 });
    const [body, status] = Array.isArray(route) ? [route[1], route[2] ?? 200] : [route.body, route.status ?? 200];
    if (status === 204) return new Response(null, { status });
    return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

export function sent(calls: Call[], method: string, url: RegExp): Call[] {
  return calls.filter((c) => c.method === method && url.test(c.url));
}
