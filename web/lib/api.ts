/**
 * API client.
 *
 * Every request goes to `/api/backend/<path>` on THIS origin, where a route
 * handler (app/api/backend/[...path]/route.ts) adds the bearer token from an
 * HttpOnly cookie and forwards it to the API. Nothing here ever holds a token:
 * a script running on this page cannot read one, which is the whole point.
 *
 * Refresh happens HERE, once per tab: a 401 the proxy marks as expired makes
 * every caller wait on the same single refresh and then replay. Any other 401
 * means the session is really over, so the page goes to the login screen once,
 * remembering where it was.
 */

import { PLANS_PATH, shouldRedirectToPlans } from "@/lib/billing";
import { loginUrlFor } from "@/lib/safe-next";
import { SESSION_EXPIRED_HEADER } from "@/lib/session";
import type { ApiErrorBody } from "@/lib/types";

/** Where the browser sends its calls: this origin, through the proxy. */
const PROXY_BASE = "/api/backend";

/**
 * The API's PUBLIC origin. Used for exactly one kind of request - file
 * uploads, see `upload` below - and to compare against URLs the API hands out
 * (the webhook screen checks that the address it shows is not an internal one).
 */
const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

/** `status` of an ApiError raised because the request never reached the API. */
export const NETWORK_ERROR_STATUS = 0;

/**
 * What to tell the reader when the API answered without a message of its own.
 *
 * "Error 502" or "Failed to fetch" means nothing to someone who runs a store:
 * the screen has to say what happened and what to do next. A sleeping server
 * (the free Render plan wakes up in ~30 s) is the most common case and the
 * one that most needs "espera y reintenta" rather than "algo salió mal".
 */
export function friendlyErrorMessage(status: number): string {
  if (status === NETWORK_ERROR_STATUS) {
    return "No hay conexión con Master Data. Revisa tu internet y pulsa Reintentar.";
  }
  if (status === 403) {
    return "Tu usuario no tiene permiso para ver esto. Pídeselo al dueño de la empresa.";
  }
  if (status === 404) {
    return "No encontramos lo que buscabas. Puede que se haya borrado o que el enlace esté incompleto.";
  }
  if (status === 408 || status === 504) {
    return "El servidor tardó demasiado en responder. Espera unos segundos y pulsa Reintentar.";
  }
  if (status === 413) {
    return "El archivo es demasiado grande. Divídelo en partes más pequeñas y súbelas por separado.";
  }
  if (status === 429) {
    return "Demasiados intentos seguidos. Espera un minuto y vuelve a intentarlo.";
  }
  if (status >= 500) {
    return "El servidor está despertando o tuvo un problema. Espera unos segundos y pulsa Reintentar.";
  }
  return "Algo no salió bien. Pulsa Reintentar; si se repite, avísanos.";
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: Record<string, unknown>;

  constructor(status: number, body: ApiErrorBody | null, fallback?: string) {
    super(
      withInvalidFields(body) ??
        body?.error?.message ??
        fallback ??
        friendlyErrorMessage(status),
    );
    this.name = "ApiError";
    this.status = status;
    this.code = body?.error?.code ?? "unknown";
    this.detail = body?.error?.detail ?? {};
  }
}

/** How the fields a form sends are called on screen. */
const FIELD_NAMES: Record<string, string> = {
  email: "el correo",
  password: "la contraseña",
  new_password: "la contraseña nueva",
  current_password: "la contraseña actual",
  full_name: "el nombre",
  name: "el nombre",
  tenant_name: "el nombre de la empresa",
  token: "el token",
  code: "el código",
};

/**
 * "Revisa los datos enviados: hay campos inválidos." does not tell a merchant
 * WHICH one. The API lists them in `detail.fields`; name the ones we know so
 * the message points at the box to fix. Unknown fields keep the API's text.
 */
export function withInvalidFields(body: ApiErrorBody | null): string | null {
  if (body?.error?.code !== "validation_error") return null;
  const fields = body.error.detail?.fields;
  if (!Array.isArray(fields)) return null;
  const names = [
    ...new Set(
      fields
        .map((item) => FIELD_NAMES[String((item as { field?: unknown })?.field ?? "")])
        .filter((name): name is string => Boolean(name)),
    ),
  ];
  if (names.length === 0) return null;
  const list =
    names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} y ${names[names.length - 1]}`;
  return `Hay datos que no son válidos. Revisa ${list}.`;
}

/**
 * The session is gone for good. Every widget on screen would now fail one by
 * one, so go to the login screen once, remembering where the reader was.
 */
function sendToLogin(): void {
  if (typeof window === "undefined") return;
  if (window.location.pathname.startsWith("/login")) return;
  window.location.assign(loginUrlFor(window.location.pathname, window.location.search));
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

  const send = () =>
    offlineAsApiError(
      fetch(`${PROXY_BASE}${path}`, {
        ...rest,
        headers: finalHeaders,
        body: payload,
        credentials: "same-origin",
      }),
    );

  let response = await send();
  if (response.status === 401 && response.headers.get(SESSION_EXPIRED_HEADER) === "1") {
    if (await refreshSession()) response = await send();
  }

  return settle<T>(response, auth);
}

/**
 * A fetch that never left the browser (offline, DNS, the API down) rejects
 * with a bare `TypeError: Failed to fetch`, which every screen used to print
 * as-is. Turned into an ApiError so it reads like every other failure.
 * An abort is left alone: the caller cancelled it on purpose.
 */
async function offlineAsApiError(pending: Promise<Response>): Promise<Response> {
  try {
    return await pending;
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(NETWORK_ERROR_STATUS, null);
  }
}

let refreshing: Promise<boolean> | null = null;

/**
 * Rotate the session ONCE, however many requests are waiting on it.
 *
 * The API kills a refresh token the moment it is used. A dashboard opened
 * after the access token expired fires ~18 requests together; if each one
 * refreshed on its own, one would win and the other seventeen would present a
 * dead token, be told "Sesión inválida" and log the reader out. Here they all
 * share one promise. Resolves `false` when the session cannot be renewed (the
 * proxy has already cleared the cookies by then).
 */
export function refreshSession(): Promise<boolean> {
  if (!refreshing) {
    refreshing = fetch(`${PROXY_BASE}/auth/refresh`, {
      method: "POST",
      credentials: "same-origin",
    })
      .then((response) => response.ok)
      .catch(() => false)
      .finally(() => {
        refreshing = null;
      });
  }
  return refreshing;
}

/** Turn the API's answer into a value or an ApiError, the same way everywhere. */
async function settle<T>(response: Response, auth: boolean): Promise<T> {
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
    // 402 = the free month ended or the plan does not allow this (migration
    // 048). The plans screen is the only useful place to be; go there once,
    // never from the plans screen itself.
    if (
      response.status === 402 &&
      typeof window !== "undefined" &&
      shouldRedirectToPlans(window.location.pathname)
    ) {
      window.location.assign(PLANS_PATH);
    }
    throw new ApiError(response.status, errorBody);
  }

  return (await response.json()) as T;
}

// ---------------------------------------------------------------------------
// File upload: the one request that does NOT go through the proxy
// ---------------------------------------------------------------------------

/**
 * The proxy runs as a serverless function - 4.5 MB request body on Vercel,
 * 6 MB on Netlify (about 4.5 MB once a multipart body is base64-encoded on
 * the way in). A 5.8 MB wallet export died there with a 413 before the proxy
 * saw a byte of it.
 *
 * So a file goes straight to the API. The proxy still guards the session: it
 * hands out only the short-lived access token (POST /api/backend/auth/
 * upload-credential), rotating it first if it is about to expire. The refresh
 * token never leaves its HttpOnly cookie. A 401 from the API means the token
 * died mid-flight; ask for a fresh one and retry exactly once.
 */
export async function upload<T>(path: string, form: FormData): Promise<T> {
  let token = await uploadCredential();
  let response = await postDirect(path, form, token);
  if (response.status === 401) {
    token = await uploadCredential();
    response = await postDirect(path, form, token);
  }
  return settle<T>(response, true);
}

async function uploadCredential(): Promise<string> {
  // Through `request`, so an expiring token is renewed by the one shared
  // refresh rather than by a second one racing it.
  const body = await request<{ access_token: string }>("/auth/upload-credential", {
    method: "POST",
  });
  return body.access_token;
}

function postDirect(path: string, form: FormData, token: string): Promise<Response> {
  return offlineAsApiError(
    fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: form,          // the browser sets the multipart boundary
      credentials: "omit", // the API wants the bearer, and has no use for cookies
    }),
  );
}

export const api = {
  upload,
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
