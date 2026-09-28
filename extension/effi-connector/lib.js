// Lógica pura de la extensión: sin `chrome.*`, para poder probarla con
// `node --test extension/effi-connector/lib.test.mjs`.
//
// LA REGLA DE ESTE ARCHIVO: de las cookies de Effi solo salen las que están en
// la lista de sesión (CONFIG.sessionCookies). Nada de contraseñas: la extensión
// no tiene cómo leerlas y no lo intenta.

const ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789";
const CODE_LENGTH = 12;

/** "abcd efgh-jkmn" -> "ABCD-EFGH-JKMN", o null si no puede ser un código. */
export function normalizeCode(value) {
  const cleaned = String(value ?? "")
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "");
  if (cleaned.length !== CODE_LENGTH) return null;
  for (const ch of cleaned) if (!ALPHABET.includes(ch)) return null;
  return `${cleaned.slice(0, 4)}-${cleaned.slice(4, 8)}-${cleaned.slice(8)}`;
}

/**
 * De todas las cookies de Effi, las de sesión y nada más. Si hay dos con el
 * mismo nombre (dominio y subdominio), gana la del dominio principal y ruta "/".
 */
export function pickSessionCookies(cookies, names, effiDomain) {
  const picked = [];
  for (const name of names) {
    const candidates = (cookies ?? []).filter((c) => c && c.name === name && c.value);
    candidates.sort((a, b) => score(b, effiDomain) - score(a, effiDomain));
    if (candidates[0]) picked.push(candidates[0]);
  }
  return picked;
}

function score(cookie, effiDomain) {
  const domain = String(cookie.domain ?? "").replace(/^\./, "");
  return (domain === effiDomain ? 2 : 0) + (cookie.path === "/" ? 1 : 0);
}

/** Lo que viaja al servidor. Solo nombre, valor y vencimiento de cada cookie. */
export function buildPayload(code, cookies, userAgent) {
  return {
    code,
    cookies: cookies.map((c) => ({
      name: c.name,
      value: c.value,
      // chrome.cookies da segundos Unix; una cookie de sesión no tiene fecha.
      expires_at: c.session || typeof c.expirationDate !== "number" ? null : c.expirationDate,
    })),
    user_agent: String(userAgent ?? "").slice(0, 512),
  };
}

/** Respuesta del servidor -> {tono, texto} para la pantalla. */
export function describeResponse(status, body) {
  if (status >= 200 && status < 300 && body && typeof body === "object") {
    const nombre = body.connection_name ? `«${body.connection_name}»` : "tu conexión";
    if (body.connected) {
      return { tono: "ok", texto: `Listo: ${nombre} quedó conectada. ${body.summary ?? ""}`.trim() };
    }
    if (body.credential_status === "session_expired") {
      return { tono: "error", texto: body.summary || "Effi no aceptó la sesión. Entra de nuevo a Effi y genera otro código." };
    }
    return { tono: "aviso", texto: body.summary || "La sesión quedó guardada, pero falta algo. Revisa Master Data." };
  }
  const message = body?.error?.message;
  if (status === 429) {
    return { tono: "error", texto: message || "Demasiados intentos. Espera un minuto." };
  }
  if (status === 404) {
    return { tono: "error", texto: message || "Ese código no sirve. Genera uno nuevo en Master Data." };
  }
  return {
    tono: "error",
    texto: message || `Master Data respondió con un error (${status}). Inténtalo de nuevo en un momento.`,
  };
}

/** "https://api.x.com/" -> "https://api.x.com", o null si no es un origen http(s). */
export function normalizeOrigin(value) {
  try {
    const url = new URL(String(value ?? "").trim());
    if (url.protocol !== "https:" && !(url.protocol === "http:" && url.hostname === "localhost")) {
      return null;
    }
    return url.origin;
  } catch {
    return null;
  }
}
