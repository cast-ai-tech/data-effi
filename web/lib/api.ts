/**
 * API client.
 *
 * Every request goes to `/api/backend/<path>` on THIS origin, where a route
 * handler (app/api/backend/[...path]/route.ts) adds the bearer token from an
 * HttpOnly cookie and forwards it to the API. Nothing here ever holds a token:
 * a script running on this page cannot read one, which is the whole point.
 *
 * Refresh is the proxy's job too. A 401 that comes back here means the session
 * is really over, so the page goes to the login screen once, remembering where
 * it was.
 */

import type { ApiErrorBody } from "@/lib/types";

/** Where the browser sends its calls: this origin, through the proxy. */
const PROXY_BASE = "/api/backend";

/**
 * The API's PUBLIC origin. Not used to make requests any more - only to
 * compare against URLs the API hands out (the webhook screen checks that the
 * address it shows is not an internal one).
 */
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: Record<string, unknown>;

  constructor(status: number, body: ApiErrorBody | null, fallback: string) {
    super(body?.error?.message ?? fallback);
    this.name = "ApiError";
    this.status = status;
    this.code = body?.error?.code ?? "unknown";
    this.detail = body?.error?.detail ?? {};
  }
}

/**
 * The session is gone for good. Every widget on screen would now fail one by
 * one, so go to the login screen once, remembering where the reader was.
 */
function sendToLogin(): void {
  if (typeof window === "undefined") return;
  if (window.location.pathname.startsWith("/login")) return;
  const next = window.location.pathname + window.location.search;
  window.location.assign(`/login?next=${encodeURIComponent(next)}`);
}

/** End the session: the proxy revokes the refresh token and clears the cookies. */
export async function signOut(): Promise<void> {
  try {
    await request<void>("/auth/logout", { method: "POST", auth: false });
  } catch {
    // Logging out must always succeed locally, even if the API is down: the
    // proxy clears the cookies before it even reaches the API.
  }
}

// ---------------------------------------------------------------------------
// Core request
// ---------------------------------------------------------------------------

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  /** Set false for login/register/logout: a 401 there is an answer, not an expired session. */
  auth?: boolean;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, auth = true, headers, ...rest } = options;

  const finalHeaders: Record<string, string> = {
    ...(headers as Record<string, string> | undefined),
  };

  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;      // let the browser set the multipart boundary
  } else if (body !== undefined) {
    payload = JSON.stringify(body);
    finalHeaders["Content-Type"] = "application/json";
  }

  const response = await fetch(`${PROXY_BASE}${path}`, {
    ...rest,
    headers: finalHeaders,
    body: payload,
    credentials: "same-origin",
  });

  if (response.status === 401 && auth) {
    sendToLogin();
  }

  if (response.status === 204) {
    return undefined as T;
  }

  if (!response.ok) {
    let errorBody: ApiErrorBody | null = null;
    try {
      errorBody = (await response.json()) as ApiErrorBody;
    } catch {
      errorBody = null;
    }
    throw new ApiError(response.status, errorBody, `Error ${response.status}`);
  }

  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "POST", body }),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "PUT", body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "PATCH", body }),
  delete: <T>(path: string, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "DELETE" }),
};

/** Build a query string, dropping empty values. */
export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : "";
}

export { API_URL };
