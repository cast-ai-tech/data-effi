import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Route protection.
 *
 * This is a redirect for convenience, NOT a security boundary: the cookie is
 * only checked for presence, never verified. Every actual authorisation
 * decision happens in the API, which validates the JWT signature and the
 * tenant on every single request. A forged cookie gets you a login-shaped page
 * that immediately fails every call it makes.
 */

const PUBLIC_PATHS = ["/login", "/register"];
const ACCESS_COOKIE = "norte_access";
const REFRESH_COOKIE = "norte_refresh";

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  const hasSession =
    request.cookies.has(ACCESS_COOKIE) || request.cookies.has(REFRESH_COOKIE);
  const isPublic = PUBLIC_PATHS.some((path) => pathname.startsWith(path));

  if (!hasSession && !isPublic) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    // Come back where you were once you are in.
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }

  if (hasSession && isPublic) {
    const url = request.nextUrl.clone();
    url.pathname = "/global";
    url.search = "";
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\.svg$).*)"],
};
