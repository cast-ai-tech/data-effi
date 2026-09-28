/**
 * What a 401 means for the session. Shared by the proxy
 * (app/api/backend/[...path]/route.ts), which labels the 401, and the API
 * client (lib/api.ts), which reads the label. Pure, so it is unit-tested
 * without a Next request in hand.
 */

import { expiresWithin } from "@/lib/upload-transport";

/** Header the proxy sets on a 401 the page should answer by refreshing once. */
export const SESSION_EXPIRED_HEADER = "x-session-expired";

// Endpoints whose 401 is an answer about the request, not about the session:
// the API says "La contraseña actual no es correcta" with a 401. Treating that
// as an expired session sent the reader away for mistyping a password.
const ANSWER_401_PATHS = new Set(["auth/me/password"]);

// An access token this close to its `exp` may have died in transit; its 401 is
// an expiry, not a rejection.
const ACCESS_EXPIRY_GRACE_SECONDS = 30;

/**
 * - `expired`: the access token is gone or about to go, and a refresh token
 *   exists. The page refreshes once and replays.
 * - `answer`: the endpoint's 401 is about the request (wrong password), sent
 *   with a live token. Nothing to do with the session.
 * - `dead`: a live-looking token was rejected, or there is nothing to refresh
 *   with. Only logging in again helps.
 */
export function classifyUnauthorized(
  path: string,
  accessToken: string | null,
  refreshToken: string | null,
  now: number = Date.now(),
): "expired" | "answer" | "dead" {
  const accessUsable =
    accessToken !== null && !expiresWithin(accessToken, ACCESS_EXPIRY_GRACE_SECONDS, now);
  if (accessUsable && ANSWER_401_PATHS.has(path)) return "answer";
  if (!accessUsable && refreshToken) return "expired";
  return "dead";
}
