/**
 * Helpers behind the direct-to-API file upload. Pure functions, no I/O, so
 * they can be unit-tested without a Next request in hand.
 *
 * Why uploads are special: the `/api/backend` proxy runs as a serverless
 * function whose request body is capped - 4.5 MB on Vercel, 6 MB on Netlify
 * (about 4.5 MB for a multipart body, which travels base64-encoded). The page
 * therefore posts a file straight to the API (lib/api.ts `upload`), which
 * needs (a) the API's origin allowed by the CSP (middleware.ts) and (b) a
 * bearer the proxy hands out only when it is not about to expire
 * (app/api/backend route).
 */

/**
 * The origin the browser may open a connection to for uploads, derived from
 * NEXT_PUBLIC_API_URL. `null` when unset or unparsable: the CSP then stays at
 * `'self'` and the upload fails loudly instead of the policy opening a hole.
 */
export function apiOrigin(publicApiUrl: string | undefined): string | null {
  if (!publicApiUrl) return null;
  try {
    return new URL(publicApiUrl).origin;
  } catch {
    return null;
  }
}

/**
 * Whether the JWT's `exp` falls within `seconds` from now. The signature is
 * NOT checked here - the API does that on every request - this only decides
 * whether the proxy rotates the token before handing it out for an upload.
 * Unreadable counts as expiring: rotate.
 */
export function expiresWithin(token: string, seconds: number, now: number = Date.now()): boolean {
  try {
    const payload = token.split(".")[1] ?? "";
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    const exp = (JSON.parse(atob(base64)) as { exp?: unknown }).exp;
    if (typeof exp !== "number") return true;
    return exp - now / 1000 < seconds;
  } catch {
    return true;
  }
}
