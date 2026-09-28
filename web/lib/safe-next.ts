/**
 * Where to send someone after they sign in.
 *
 * `next` comes from the query string, so it is attacker-controlled: a link
 * `/login?next=https://evil.example` would otherwise bounce a freshly signed-in
 * operator - token in hand - to a site that looks like this one. Only a path on
 * this origin is honoured; anything else falls back to the dashboard.
 */
/**
 * The `/login` address that brings the reader back to exactly where they were.
 *
 * The query string is part of "where": `/co?from=2026-09-01&tab=logistica` is a
 * different screen from `/co`, and a session that expires while Osvaldo reads
 * last week's deliveries must not drop him on the whole history after he signs
 * in again. The page's own parameters never leak onto the login URL itself.
 */
export function loginUrlFor(pathname: string, search = ""): string {
  const query = search && search !== "?" ? (search.startsWith("?") ? search : `?${search}`) : "";
  const params = new URLSearchParams({ next: `${pathname}${query}` });
  return `/login?${params.toString()}`;
}

export function safeNextPath(raw: string | null | undefined, fallback = "/global"): string {
  if (!raw) return fallback;
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) return fallback;
  if (raw.includes("://") || /[\r\n]/.test(raw)) return fallback;
  return raw;
}
