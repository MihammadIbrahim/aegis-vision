import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import type { Role, Session } from "../api/types";
import { StaticAuthProvider } from "../auth/AuthContext";
import { ToastProvider } from "../components/ui";

export function sessionFor(role: Role): Session {
  return { user_id: "X", full_name: role === "ADMIN" ? "Admin A" : "Supervisor S", role, dashboard: null };
}

/** Renders ``ui`` as the given role. With ``route`` (e.g. "/personnel/:id") the page gets its URL params. */
export function renderAs(role: Role, ui: ReactElement, path = "/", route?: string) {
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
      <StaticAuthProvider session={sessionFor(role)}>
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
