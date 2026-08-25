import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { apiOrigin } from "@/lib/upload-transport";

/**
 * Two jobs, one pass.
 *
 * ROUTE PROTECTION. A redirect for convenience, NOT a security boundary: the
 * cookie is only checked for presence, never verified. Every actual
 * authorisation decision happens in the API, which validates the JWT signature
 * and the tenant on every single request. A forged cookie gets you a
 * login-shaped page that immediately fails every call it makes. The session
 * cookies are HttpOnly (set by app/api/backend); middleware runs on the server
 * and can read them all the same.
 *
 * CONTENT-SECURITY-POLICY. Issued here rather than in next.config.ts because
 * it carries a per-request nonce. Next reads the nonce from this request
 * header and stamps it on its own inline scripts, so `script-src` needs no
 * `'unsafe-inline'`: an injected `<script>` has no nonce and does not run.
 * `connect-src 'self'` alone is enough - the browser only ever talks to this
 * origin, the proxy does the rest - so a script that somehow ran could not
 * send anything anywhere else either.
 */

const PUBLIC_PATHS = ["/login", "/register"];
// Keep in sync with app/api/backend/[...path]/route.ts.
const ACCESS_COOKIE = "masterdata_access";
const REFRESH_COOKIE = "masterdata_refresh";
// TODO(rebrand): retirar LEGACY_* después de 2026-09-15 (REFRESH_TTL de 14 días).
const LEGACY_ACCESS_COOKIE = "dataeffi_access";
const LEGACY_REFRESH_COOKIE = "dataeffi_refresh";

// The one exception to "the browser only talks to this origin": file uploads
// go straight to the API (lib/api.ts `upload`), because the proxy runs as a
// serverless function with a 6 MB body cap. `connect-src` must name the API
// origin or the browser refuses the request before it leaves.
const API_ORIGIN = apiOrigin(process.env.NEXT_PUBLIC_API_URL);

function contentSecurityPolicy(nonce: string): string {
  return [
    "default-src 'self'",
    API_ORIGIN ? `connect-src 'self' ${API_ORIGIN}` : "connect-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com data:",
    "img-src 'self' data: blob:",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
  ].join("; ");
}

function withCsp(request: NextRequest, redirectTo?: URL): NextResponse {
  const nonce = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(16))));
  const csp = contentSecurityPolicy(nonce);

  if (redirectTo) {
    const redirect = NextResponse.redirect(redirectTo);
    redirect.headers.set("Content-Security-Policy", csp);
    return redirect;
  }

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  const hasSession =
    request.cookies.has(ACCESS_COOKIE) ||
    request.cookies.has(REFRESH_COOKIE) ||
    request.cookies.has(LEGACY_ACCESS_COOKIE) ||
    request.cookies.has(LEGACY_REFRESH_COOKIE);
  const isPublic = PUBLIC_PATHS.some((path) => pathname.startsWith(path));

  if (!hasSession && !isPublic) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    // Come back where you were once you are in.
    url.searchParams.set("next", pathname);
    return withCsp(request, url);
  }

  if (hasSession && isPublic) {
    const url = request.nextUrl.clone();
    url.pathname = "/global";
    url.search = "";
    return withCsp(request, url);
  }

  return withCsp(request);
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\.svg$).*)"],
};
