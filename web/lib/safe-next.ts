/**
 * Where to send someone after they sign in.
 *
 * `next` comes from the query string, so it is attacker-controlled: a link
 * `/login?next=https://evil.example` would otherwise bounce a freshly signed-in
 * operator - token in hand - to a site that looks like this one. Only a path on
 * this origin is honoured; anything else falls back to the dashboard.
 */
export function safeNextPath(raw: string | null | undefined, fallback = "/global"): string {
  if (!raw) return fallback;
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) return fallback;
  if (raw.includes("://") || /[\r\n]/.test(raw)) return fallback;
  return raw;
}
