import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * The browser never talks to the API directly and never sees a token.
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
 * Refresh happens HERE, once, when the API answers 401: the refresh token is
 * presented, both cookies are rotated, and the original request is replayed.
 * The page sees either the real answer or a 401 that means "log in again".
 */

// Server-side only. In Docker this is the internal service name
// (`http://api:8000`); on Netlify it is the public Render URL. Falls back to
// the public variable so a local `npm run dev` needs no extra setting.
const API_URL = (
  process.env.API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8000"
).replace(/\/$/, "");

const ACCESS_COOKIE = "dataeffi_access";
const REFRESH_COOKIE = "dataeffi_refresh";
const REFRESH_TTL_SECONDS = 60 * 60 * 24 * 14;

// Endpoints whose 2xx body carries a token pair to be moved into cookies.
const TOKEN_ENDPOINTS = new Set([
  "auth/login",
  "auth/register",
  "auth/refresh",
  "auth/switch",
  "auth/accept-invite",
]);

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
}

function clearSessionCookies(response: NextResponse, request: NextRequest): void {
  response.cookies.set(ACCESS_COOKIE, "", cookieOptions(request, 0));
  response.cookies.set(REFRESH_COOKIE, "", cookieOptions(request, 0));
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
  // socket peer is always us, so the original chain travels in the header the
  // API already reads (last hop wins, see api/deps.py).
  const forwardedFor = request.headers.get("x-forwarded-for");
  if (forwardedFor) headers.set("x-forwarded-for", forwardedFor);
  // Better: tell the API who the browser is, signed with a secret only the
  // two servers know (PROXY_SHARED_SECRET on both). The first hop of the chain
  // is the browser as the platform in front of us (Netlify, Docker's network)
  // saw it.
  const secret = process.env.PROXY_SHARED_SECRET;
  const clientIp = forwardedFor?.split(",")[0]?.trim();
  if (secret && clientIp) {
    headers.set("x-proxy-secret", secret);
    headers.set("x-client-ip", clientIp);
  }
  if (accessToken) headers.set("authorization", `Bearer ${accessToken}`);

  return fetch(`${API_URL}/${path}${request.nextUrl.search}`, {
    method: request.method,
    headers,
    body: body && body.byteLength > 0 ? body : undefined,
    redirect: "manual",
    cache: "no-store",
  });
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
  const accessToken = request.cookies.get(ACCESS_COOKIE)?.value ?? null;
  const refreshToken = request.cookies.get(REFRESH_COOKIE)?.value ?? null;

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

  const body = request.method === "GET" || request.method === "HEAD" ? null : await request.arrayBuffer();

  let upstream = await forward(request, path, body, accessToken);
  let rotated: TokenBody | null = null;

  // Silent refresh, once, then replay.
  if (upstream.status === 401 && refreshToken && !TOKEN_ENDPOINTS.has(path)) {
    rotated = await refresh(request, refreshToken);
    if (rotated?.access_token) {
      upstream = await forward(request, path, body, rotated.access_token);
    } else {
      const response = await passThrough(upstream);
      clearSessionCookies(response, request);
      return response;
    }
  }

  if (TOKEN_ENDPOINTS.has(path) && upstream.ok) {
    const tokens = (await upstream.json()) as TokenBody;
    const response = NextResponse.json(stripTokens(tokens), { status: upstream.status });
    setSessionCookies(response, request, tokens);
    return response;
  }

  const response = await passThrough(upstream);
  if (rotated) setSessionCookies(response, request, rotated);

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
