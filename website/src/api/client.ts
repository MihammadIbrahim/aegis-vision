// A thin wrapper around fetch for the Core API (/api/v1). The access token is kept in
// sessionStorage, so it disappears when the browser tab closes. Permissions are always enforced
// by the server; the UI only hides actions a role cannot use.

const TOKEN_KEY = "cctv.accessToken";
const BASE = "/api/v1";

export interface FieldError {
  field?: string | null;
  code?: string;
  message: string;
  column_index?: number;
  column_name?: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly errors: FieldError[];

  constructor(status: number, message: string, code?: string, errors: FieldError[] = []) {
    super(message);
    this.status = status;
    this.code = code;
    this.errors = errors;
  }
}

/** Called when a signed-in request shows the session is over: any 401 (expired, password changed), or
 * 403 ORGANIZATION_INACTIVE (the Super Admin suspended or deactivated the organization). ``code`` is the server's. */
type SessionEndHandler = (code?: string) => void;
let unauthorizedHandler: SessionEndHandler | null = null;

export function onUnauthorized(handler: SessionEndHandler | null): void {
  unauthorizedHandler = handler;
}

export function getToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) sessionStorage.setItem(TOKEN_KEY, token);
  else sessionStorage.removeItem(TOKEN_KEY);
}

export interface RequestOptions {
  method?: string;
  json?: unknown;
  form?: FormData;
  /** Arrays become repeated parameters (e.g. ?status=OPEN&status=RESOLVED). */
  query?: Record<string, string | number | boolean | null | undefined | readonly string[]>;
}

export function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (Array.isArray(value)) {
      for (const item of value) if (item !== "") params.append(key, String(item));
    } else if (value !== null && value !== undefined && value !== "") params.set(key, String(value));
  }
  const text = params.toString();
  return `${BASE}${path}${text ? `?${text}` : ""}`;
}

export async function parseError(response: Response): Promise<ApiError> {
  let message = `Request failed (${response.status}).`;
  let code: string | undefined;
  let errors: FieldError[] = [];
  try {
    const body = await response.json();
    if (typeof body.detail === "string") message = body.detail;
    else if (Array.isArray(body.detail)) {
      // FastAPI request validation: never echoes submitted values.
      message = "Some fields are missing or not valid.";
      errors = body.detail.map((d: { loc?: unknown[]; msg?: string }) => ({
        field: Array.isArray(d.loc) ? String(d.loc[d.loc.length - 1]) : null,
        message: d.msg ?? "Invalid value",
      }));
    }
    if (typeof body.code === "string") code = body.code;
    if (Array.isArray(body.errors)) errors = body.errors;
  } catch {
    // Not JSON: keep the generic message.
  }
  return new ApiError(response.status, message, code, errors);
}

async function send(path: string, options: RequestOptions = {}): Promise<Response> {
  const headers = new Headers();
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body: BodyInit | undefined;
  if (options.form) body = options.form;
  else if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.json);
  }
  const response = await fetch(buildUrl(path, options.query), {
    method: options.method ?? (body ? "POST" : "GET"),
    headers,
    body,
  });
  if (!response.ok) {
    const error = await parseError(response);
    if (token && (response.status === 401 || (response.status === 403 && error.code === "ORGANIZATION_INACTIVE"))) {
      unauthorizedHandler?.(error.code ?? "SESSION_EXPIRED");
    }
    throw error;
  }
  return response;
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await send(path, options);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function apiBlob(path: string, options: RequestOptions = {}): Promise<Blob> {
  return (await send(path, options)).blob();
}

/** Downloads a file from the API (with the session's token) and saves it. */
export async function download(path: string, filename: string): Promise<void> {
  const blob = await apiBlob(path);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
