import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { SESSION_EXPIRED_HEADER, classifyUnauthorized } from "@/lib/session";
import { expiresWithin } from "@/lib/upload-transport";

/**
 * The browser never talks to the API directly and never sees a token - with
 * one exception, file uploads, explained at `auth/upload-credential` below.
 *
 * Every call from the client goes to `/api/backend/<path>` on this origin. This
 * handler adds the bearer token from an HttpOnly cookie, forwards the request,
 * and hands the answer back. The token endpoints (`/auth/login`, `/auth/refresh`,
 * `/auth/switch`, ...) are the exception: their JSON is read here, the tokens go
 * into HttpOnly cookies, and the body reaches the page WITHOUT them.
 *
 * Why: an XSS on this site used to be able to read the refresh cookie and hold
 * the session for fourteen days from anywhere. `HttpOnly` makes that cookie
 * invisible to scripts, and a cookie a script cannot read is a cookie it cannot
 * exfiltrate. The price is one hop through this function per request.
 *
 * REFRESH IS THE PAGE'S JOB, NOT THIS HANDLER'S. It used to happen here, on
 * every 401. But the API rotates the refresh token on use (the presented one
 * dies), and a dashboard opened after the access token expired fires ~18
 * requests at once: every one of them tried the SAME refresh token in
 * parallel, one won, the rest got "Sesión inválida", cleared the cookies the
 * winner had just written and sent the reader to the login screen. Serverless
 * invocations share nothing, so the only place that can refresh ONCE is the
 * browser tab (lib/api.ts `refreshSession`).
 *
 * So a 401 leaves here in one of three shapes:
 *  - `x-session-expired: 1`: the access token is missing or about to expire
 *    and a refresh token exists. The page refreshes once and replays.
 *  - an "answer" 401 (wrong current password): passed through untouched.
 *  - anything else: the session is dead. The cookies are cleared HERE, because
 *    a login screen reached with the cookies still set is bounced straight
 *    back to the dashboard by middleware.ts - a redirect loop.
 */

// Server-side only. In Docker this is the internal service name
// (`http://api:8000`); on Vercel it is the public Render URL. Falls back to
// the public variable so a local `npm run dev` needs no extra setting.
const API_URL = (
  process.env.API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8000"
).replace(/\/$/, "");

// Keep in sync with middleware.ts.
const ACCESS_COOKIE = "masterdata_access";
const REFRESH_COOKIE = "masterdata_refresh";
// The pre-rebrand names are still read for one release so nobody is logged
// out by the rename: the first silent refresh rewrites the session under the
// new names and expires the old ones.
// TODO(rebrand): retirar LEGACY_* después de 2026-09-15 (REFRESH_TTL de 14 días).
const LEGACY_ACCESS_COOKIE = "dataeffi_access";
const LEGACY_REFRESH_COOKIE = "dataeffi_refresh";
const REFRESH_TTL_SECONDS = 60 * 60 * 24 * 14;
// An upload credential must outlive the upload it is handed out for.
const UPLOAD_CREDENTIAL_MIN_TTL_SECONDS = 120;

// Endpoints whose 2xx body carries a token pair to be moved into cookies.
const TOKEN_ENDPOINTS = new Set([
  "auth/login",
  "auth/register",
  "auth/refresh",
  "auth/switch",
  "auth/accept-invite",
]);
// The ones that authenticate with credentials in the body, not with a session.
const CREDENTIAL_ENDPOINTS = new Set(["auth/login", "auth/register"]);

// Request headers worth forwarding. Never `cookie` (the API has no use for it
// and it would leak our session cookies to a third host in a misconfiguration)
// and never `host`.
const FORWARDED_REQUEST_HEADERS = ["content-type", "accept", "accept-language", "x-filename", "x-kind"];

interface TokenBody {
  access_token?: string;
  refresh_token?: string;
  expires_in?: number;
  [key: string]: unknown;
}

function cookieOptions(request: NextRequest, maxAge: number) {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: request.nextUrl.protocol === "https:",
    path: "/",
    maxAge,
  };
}

function setSessionCookies(response: NextResponse, request: NextRequest, tokens: TokenBody): void {
  if (tokens.access_token) {
    response.cookies.set(
      ACCESS_COOKIE,
      tokens.access_token,
      cookieOptions(request, tokens.expires_in ?? 15 * 60),
    );
  }
  if (tokens.refresh_token) {
    response.cookies.set(
      REFRESH_COOKIE,
      tokens.refresh_token,
      cookieOptions(request, REFRESH_TTL_SECONDS),
    );
  }
  // Once the session is written under the new names, the old ones go.
  if (request.cookies.has(LEGACY_ACCESS_COOKIE) || request.cookies.has(LEGACY_REFRESH_COOKIE)) {
    response.cookies.set(LEGACY_ACCESS_COOKIE, "", cookieOptions(request, 0));
    response.cookies.set(LEGACY_REFRESH_COOKIE, "", cookieOptions(request, 0));
  }
}

function clearSessionCookies(response: NextResponse, request: NextRequest): void {
  response.cookies.set(ACCESS_COOKIE, "", cookieOptions(request, 0));
  response.cookies.set(REFRESH_COOKIE, "", cookieOptions(request, 0));
  response.cookies.set(LEGACY_ACCESS_COOKIE, "", cookieOptions(request, 0));
  response.cookies.set(LEGACY_REFRESH_COOKIE, "", cookieOptions(request, 0));
}

function stripTokens(body: TokenBody): Record<string, unknown> {
  const rest: Record<string, unknown> = { ...body };
  delete rest.access_token;
  delete rest.refresh_token;
  return rest;
}

async function forward(
  request: NextRequest,
  path: string,
  body: ArrayBuffer | null,
  accessToken: string | null,
): Promise<Response> {
  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  // The API rate-limits and audits by caller address; behind this proxy the
  // socket peer is always us. Tell it who the browser is, signed with a secret
  // only the two servers know (PROXY_SHARED_SECRET on both).
  //
  // WHICH address: never the first entry of X-Forwarded-For - the browser can
  // write that one itself and pick its own rate-limit bucket. The platform in
  // front of us sets a header the client cannot forge (Vercel and most
  // reverse proxies: x-real-ip; Netlify: x-nf-client-connection-ip); failing
  // those, the LAST hop of the chain is the one the nearest trusted proxy
  // appended, which is the same rule api/deps.py applies.
  const secret = process.env.PROXY_SHARED_SECRET;
  const clientIp = trustedClientIp(request);
  if (secret && clientIp) {
    headers.set("x-proxy-secret", secret);
    headers.set("x-client-ip", clientIp);
    // With a signed identity the chain is redundant; without one, forward
    // ONLY the trusted address so the API's "last hop" is never client-typed.
  }
  if (clientIp) headers.set("x-forwarded-for", clientIp);
  if (accessToken) headers.set("authorization", `Bearer ${accessToken}`);

  return fetch(`${API_URL}/${path}${request.nextUrl.search}`, {
    method: request.method,
    headers,
    body: body && body.byteLength > 0 ? body : undefined,
    redirect: "manual",
    cache: "no-store",
  });
}

function trustedClientIp(request: NextRequest): string | null {
  for (const name of ["x-nf-client-connection-ip", "x-real-ip"]) {
    const value = request.headers.get(name)?.trim();
    if (value) return value.slice(0, 64);
  }
  const chain = request.headers.get("x-forwarded-for") ?? "";
  const hops = chain.split(",").map((hop) => hop.trim()).filter(Boolean);
  return hops.length ? hops[hops.length - 1].slice(0, 64) : null;
}

async function refresh(request: NextRequest, refreshToken: string): Promise<TokenBody | null> {
  const response = await fetch(`${API_URL}/auth/refresh`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
    cache: "no-store",
  });
  if (!response.ok) return null;
  return (await response.json()) as TokenBody;
}

async function passThrough(upstream: Response): Promise<NextResponse> {
  const headers = new Headers();
  const contentType = upstream.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  headers.set("cache-control", "no-store");
  if (upstream.status === 204) {
    return new NextResponse(null, { status: 204, headers });
  }
  return new NextResponse(await upstream.arrayBuffer(), { status: upstream.status, headers });
}

async function handle(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path: segments } = await context.params;
  const path = segments.join("/");
  const accessToken =
    request.cookies.get(ACCESS_COOKIE)?.value ??
    request.cookies.get(LEGACY_ACCESS_COOKIE)?.value ??
    null;
  const refreshToken =
    request.cookies.get(REFRESH_COOKIE)?.value ??
    request.cookies.get(LEGACY_REFRESH_COOKIE)?.value ??
    null;

  // Logout: the refresh token lives only here, so the page cannot send it.
  if (path === "auth/logout") {
    if (refreshToken) {
      await fetch(`${API_URL}/auth/logout`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
        cache: "no-store",
      }).catch(() => undefined);
    }
    const response = new NextResponse(null, { status: 204 });
    clearSessionCookies(response, request);
    return response;
  }

  // Refresh asked for explicitly: same rotation as the silent one below.
  if (path === "auth/refresh") {
    const tokens = refreshToken ? await refresh(request, refreshToken) : null;
    if (!tokens) {
      const response = NextResponse.json(
        { error: { code: "unauthorized", message: "Sesión expirada. Vuelve a iniciar sesión.", detail: {} } },
        { status: 401 },
      );
      clearSessionCookies(response, request);
      return response;
    }
    const response = NextResponse.json(stripTokens(tokens));
    setSessionCookies(response, request, tokens);
    return response;
  }

  // File uploads do NOT go through this proxy. On Vercel this handler is a
  // serverless function whose request body is capped at 4.5 MB (Netlify:
  // 6 MB, about 4.5 MB for multipart, which travels base64-encoded), so
  // anything bigger dies with a 413 before a line of this code runs. The
  // page posts the file straight to the API instead and asks here for the
  // credential to do it:
  // the short-lived ACCESS token, never the refresh one. The refresh token is
  // the one an XSS could turn into a fortnight of access, and it stays in
  // its HttpOnly cookie.
  if (path === "auth/upload-credential") {
    if (request.method !== "POST") {
      return new NextResponse(null, { status: 405 });
    }
    // A token about to expire would only earn the upload a 401 halfway through
    // sending 20 MB. The page rotates it (lib/api.ts `refreshSession`, the
    // one refresh per tab - rotating here too would race it) and asks again.
    if (accessToken && !expiresWithin(accessToken, UPLOAD_CREDENTIAL_MIN_TTL_SECONDS)) {
      return NextResponse.json({ access_token: accessToken }, { headers: { "cache-control": "no-store" } });
    }
    const response = NextResponse.json(
      { error: { code: "unauthorized", message: "Sesión expirada. Vuelve a iniciar sesión.", detail: {} } },
      { status: 401, headers: { "cache-control": "no-store" } },
    );
    if (refreshToken) response.headers.set(SESSION_EXPIRED_HEADER, "1");
    else clearSessionCookies(response, request);
    return response;
  }

  const body = request.method === "GET" || request.method === "HEAD" ? null : await request.arrayBuffer();

  let upstream: Response;
  try {
    upstream = await forward(request, path, body, accessToken);
  } catch {
    // The API did not answer at all: asleep, redeploying, or API_URL points
    // nowhere. Say so in the API's own error shape - a bare 500 page reaches
    // the reader as "Error 500", which reads like a bug in the screen.
    return NextResponse.json(
      {
        error: {
          code: "upstream_unreachable",
          message: "No se pudo conectar con el servidor de datos. Intenta de nuevo en un momento.",
          detail: {},
        },
      },
      { status: 502, headers: { "cache-control": "no-store" } },
    );
  }

  if (TOKEN_ENDPOINTS.has(path) && upstream.ok) {
    const tokens = (await upstream.json()) as TokenBody;
    const response = NextResponse.json(stripTokens(tokens), { status: upstream.status });
    setSessionCookies(response, request, tokens);
    return response;
  }

  const response = await passThrough(upstream);

  // A 401 from login/register is "wrong password", not a session verdict.
  if (upstream.status === 401 && !CREDENTIAL_ENDPOINTS.has(path)) {
    const verdict = classifyUnauthorized(path, accessToken, refreshToken);
    if (verdict === "expired") response.headers.set(SESSION_EXPIRED_HEADER, "1");
    if (verdict === "dead") clearSessionCookies(response, request);
    return response;
  }

  // The API revokes every session on a password change, this one included.
  if (path === "auth/me/password" && upstream.ok) clearSessionCookies(response, request);

  return response;
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;

export const dynamic = "force-dynamic";
