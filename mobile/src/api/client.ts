import { API_BASE_URL, API_PREFIX, REQUEST_TIMEOUT_MS } from "../config";
import type { ApiErrorBody } from "./types";

/** Codes that end the Guard's session wherever they occur (decisions D-018, D-019). */
export const SESSION_ENDING_CODES = new Set(["ASSIGNMENT_CHANGED", "ORGANIZATION_INACTIVE", "SESSION_EXPIRED"]);

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly retryAfterSeconds: number | null;
  readonly fieldErrors: Record<string, string> | null;

  constructor(
    status: number,
    message: string,
    code: string | null = null,
    retryAfterSeconds: number | null = null,
    fieldErrors: Record<string, string> | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
    this.fieldErrors = fieldErrors;
  }

  /** No answer from the server (offline, DNS, timeout). */
  get isNetworkError(): boolean {
    return this.status === 0;
  }
}

export interface SessionEnd {
  code: string;
  message: string;
}

type TokenGetter = () => string | null;
type SessionEndHandler = (end: SessionEnd) => void;

let getToken: TokenGetter = () => null;
let onSessionEnd: SessionEndHandler = () => undefined;

/** Wired once by the SessionProvider. */
export function configureApiClient(options: { getToken: TokenGetter; onSessionEnd: SessionEndHandler }): void {
  getToken = options.getToken;
  onSessionEnd = options.onSessionEnd;
}

function messageOf(body: ApiErrorBody | null, status: number): string {
  if (body && typeof body.detail === "string" && body.detail) return body.detail;
  if (body && Array.isArray(body.detail) && body.detail.length > 0) {
    return body.detail.map((d) => d.msg).join(" ");
  }
  if (status === 429) return "Too many attempts. Please wait and try again.";
  if (status >= 500) return "The server could not complete the request. Please try again.";
  return "The request could not be completed.";
}

export interface RequestOptions {
  body?: unknown;
  /** Send the access token (default). Sign-in and password-reset routes are public. */
  auth?: boolean;
  timeoutMs?: number;
}

export async function apiRequest<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  const { body, auth = true, timeoutMs = REQUEST_TIMEOUT_MS } = options;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = auth ? getToken() : null;
  if (auth && token) headers.Authorization = `Bearer ${token}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${API_PREFIX}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch {
    throw new ApiError(0, "No connection to the server. Check your internet connection.", "NETWORK_ERROR");
  } finally {
    clearTimeout(timer);
  }

  if (response.status === 204) return undefined as T;
  let parsed: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }
  if (response.ok) return parsed as T;

  const errorBody = (parsed && typeof parsed === "object" ? parsed : null) as ApiErrorBody | null;
  const code = errorBody?.code ?? null;
  const message = messageOf(errorBody, response.status);
  const error = new ApiError(
    response.status,
    message,
    code,
    errorBody?.retry_after_seconds ?? null,
    errorBody?.errors ?? null,
  );
  if (auth && token) {
    // A signed-in request refused because the session itself is no longer valid: sign out everywhere.
    if (response.status === 401) {
      onSessionEnd({ code: code ?? "SESSION_EXPIRED", message });
    } else if (response.status === 403 && (code === "ORGANIZATION_INACTIVE" || code === "SUPER_ADMIN_NOT_ALLOWED")) {
      onSessionEnd({ code, message });
    }
  }
  throw error;
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => apiRequest<T>("GET", path, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiRequest<T>("POST", path, { ...options, body: body ?? {} }),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) => apiRequest<T>("PUT", path, { ...options, body }),
  delete: <T>(path: string, options?: RequestOptions) => apiRequest<T>("DELETE", path, options),
};

/** A readable message for any thrown value. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong. Please try again.";
}
