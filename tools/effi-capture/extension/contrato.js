/**
 * El corazón: deducir el contrato de login SIN tocar un solo valor.
 *
 * LA REGLA, Y NO TIENE EXCEPCIONES
 * --------------------------------
 * Este archivo lee nombres de campos, rutas y encabezados. NUNCA lee, guarda ni
 * devuelve el VALOR de un campo, una cookie o un encabezado. Una contraseña
 * entra por aquí en cada captura y no debe salir por ningún lado: ni al popup,
 * ni al portapapeles, ni a la consola, ni a memoria más allá del instante en que
 * se mira su nombre.
 *
 * Si alguna vez hace falta ver un valor para depurar algo, la respuesta es
 * mirarlo en DevTools a mano. En el momento en que este archivo imprime un
 * valor, deja de ser seguro darle su salida a nadie, que es justamente lo único
 * que lo hace útil.
 *
 * COMPARTIDO A PROPÓSITO
 * ----------------------
 * Lo usan la extensión (captura en vivo) y analizador.html (lee un .har). Son
 * dos caminos hacia el mismo dato, y si cada uno tuviera su propia lógica
 * acabarían dando respuestas distintas sobre el mismo Effi - que es la clase de
 * discrepancia que cuesta un día averiguar.
 *
 * Espejo en Python: scripts/extract_effi_contract.py. Si cambias las pistas de
 * abajo, cámbialas en los dos.
 */

/* eslint-env browser, webextensions */

// Cómo reconocer para qué sirve un campo, solo por su nombre.
const USER_HINTS = [
  "usuario",
  "user",
  "login",
  "email",
  "correo",
  "documento",
  "cedula",
  "nit",
];
const PASS_HINTS = [
  "clave",
  "pass",
  "password",
  "contrasena",
  "contraseña",
  "pwd",
  "secret",
];
const CSRF_HINTS = [
  "csrf",
  "token",
  "authenticity",
  "_token",
  "xsrf",
  "nonce",
  "state",
];

// Palabras que en una ruta delatan una descarga de reporte.
const EXPORT_HINTS = [
  "export",
  "reporte",
  "report",
  "descarga",
  "download",
  "excel",
  "xls",
  "csv",
];

// Tipos de contenido que son una planilla de verdad y no una página de error.
const SHEET_TYPES = [
  "spreadsheet",
  "excel",
  "csv",
  "octet-stream",
  "vnd.ms-excel",
];

// Nombres de cookie que suenan a sesión, para elegir entre varias.
const SESSION_HINTS = ["session", "sesion", "sess", "auth", "sid", "token"];

/**
 * Para qué sirve un campo, deducido de su nombre. Nunca de su valor.
 *
 * El orden importa: CSRF se comprueba primero porque un campo llamado
 * "user_token" es un token, no un usuario, y clasificarlo al revés haría que la
 * extensión reportara el campo equivocado como el del usuario.
 */
function clasificarCampo(nombre) {
  const bajo = String(nombre || "").toLowerCase();
  if (!bajo) return "";
  if (CSRF_HINTS.some((h) => bajo.includes(h))) return "csrf";
  if (PASS_HINTS.some((h) => bajo.includes(h))) return "password";
  if (USER_HINTS.some((h) => bajo.includes(h))) return "username";
  return "";
}

/** ¿Estos campos, juntos, son un formulario de entrar? */
function pareceLogin(campos) {
  const roles = new Set(campos.map((c) => c.role));
  return roles.has("username") && roles.has("password");
}

/** El nombre del campo que cumple un rol, o "" si no hay. */
function campoConRol(campos, rol) {
  const hallado = campos.find((c) => c.role === rol);
  return hallado ? hallado.name : "";
}

/**
 * Elige, entre varias cookies, la que más parece la de sesión.
 * Si ninguna lo parece, devuelve la primera: es mejor una candidata revisable
 * que un hueco silencioso.
 */
function elegirCookieDeSesion(nombres) {
  if (!nombres || !nombres.length) return "";
  const sospechosa = nombres.find((n) =>
    SESSION_HINTS.some((h) => String(n).toLowerCase().includes(h)),
  );
  return sospechosa || nombres[0];
}

/** ¿Esta ruta / tipo de contenido parece una descarga de reporte? */
function pareceExportacion(ruta, tipoContenido) {
  const r = String(ruta || "").toLowerCase();
  const t = String(tipoContenido || "").toLowerCase();
  return (
    EXPORT_HINTS.some((h) => r.includes(h)) ||
    SHEET_TYPES.some((s) => t.includes(s))
  );
}

/**
 * El texto que la persona copia y pega en el chat.
 *
 * `contrato` lleva: base, ruta, campoUsuario, campoClave, campoCsrf, carrier,
 * carrierNombre, estado, otrosCampos[], exportaciones[].
 */
function formatearContrato(contrato) {
  const linea = "=".repeat(64);
  const partes = [];

  partes.push(linea);
  partes.push("CONTRATO DE LOGIN DE EFFI");
  partes.push(linea);

  if (!contrato.ruta) {
    partes.push("");
    partes.push("NO SE CAPTURÓ EL LOGIN.");
    partes.push("");
    partes.push(
      "Un login es un envío que lleva juntos un campo de usuario y uno",
    );
    partes.push("de contraseña. Si no aparece, casi siempre es una de estas:");
    partes.push(
      "  · La grabación empezó DESPUÉS de entrar. Cierra sesión en Effi",
    );
    partes.push("    y vuelve a grabar desde la pantalla de entrar.");
    partes.push("  · Effi entró solo, con una sesión que ya estaba abierta.");
    partes.push("  · Effi usa un dominio que la extensión no vigila (mira el");
    partes.push("    apartado «Otro dominio» de las instrucciones).");
    return partes.join("\n");
  }

  partes.push(`  base            ${contrato.base || "(no se detectó)"}`);
  partes.push(`  ruta            ${contrato.ruta}`);
  partes.push(
    `  campo usuario   ${contrato.campoUsuario || "(no se detectó)"}`,
  );
  partes.push(`  campo clave     ${contrato.campoClave || "(no se detectó)"}`);
  partes.push(`  campo CSRF      ${contrato.campoCsrf || "(ninguno)"}`);
  partes.push(
    `  sesión vuelve   ${contrato.carrier || "(no se detectó)"}  ${contrato.carrierNombre || ""}`,
  );
  partes.push(`  respondió       HTTP ${contrato.estado || "?"}`);
  partes.push(
    `  otros campos    ${(contrato.otrosCampos || []).join(", ") || "(ninguno)"}`,
  );

  if (!contrato.carrier) {
    partes.push("");
    partes.push("  AVISO: no se vio cómo vuelve la sesión. Puede que Effi la");
    partes.push(
      "  entregue en una petición posterior, o dentro del cuerpo de la",
    );
    partes.push(
      "  respuesta (que esta herramienta no lee, a propósito). Manda",
    );
    partes.push(
      "  igual esta captura: con las descargas de abajo suele bastar.",
    );
  }

  partes.push("");
  partes.push(linea);
  partes.push("PARA EL .env DEL SERVIDOR");
  partes.push(linea);
  partes.push(`EFFI_BASE_URL=${contrato.base || ""}`);
  partes.push(`EFFI_LOGIN_PATH=${contrato.ruta}`);
  partes.push(`EFFI_LOGIN_USER_FIELD=${contrato.campoUsuario || ""}`);
  partes.push(`EFFI_LOGIN_PASS_FIELD=${contrato.campoClave || ""}`);
  partes.push(`EFFI_LOGIN_CSRF_FIELD=${contrato.campoCsrf || ""}`);
  partes.push(`EFFI_SESSION_CARRIER=${contrato.carrier || ""}`);
  if (contrato.carrier === "cookie") {
    partes.push(`EFFI_SESSION_COOKIE=${contrato.carrierNombre || ""}`);
  } else if (contrato.carrier === "json") {
    partes.push(`EFFI_TOKEN_JSON_KEY=${contrato.carrierNombre || ""}`);
  }

  const exps = contrato.exportaciones || [];
  partes.push("");
  partes.push(linea);
  partes.push(`DESCARGAS DE REPORTES (${exps.length})`);
  partes.push(linea);
  if (exps.length) {
    for (const e of exps) {
      partes.push(`  ${e.metodo || "GET"}   ${e.ruta}`);
      partes.push(`        parámetros: ${e.params || "(ninguno)"}`);
      partes.push(
        `        devolvió:   HTTP ${e.estado || "?"}  ${e.tipo || "(sin tipo)"}`,
      );
    }
  } else {
    partes.push(
      "  Ninguna. Si querías capturarlas, entra a cada reporte y pulsa",
    );
    partes.push("  «Exportar» una vez por reporte, con la grabación andando.");
  }

  partes.push("");
  partes.push(linea);
  partes.push("NADA DE ESTO ES UN SECRETO");
  partes.push(linea);
  partes.push(
    "  Son nombres de campos y rutas. Ni una contraseña, ni una cookie,",
  );
  partes.push(
    "  ni un valor: esta herramienta no los lee. Se puede pegar en un",
  );
  partes.push("  chat con tranquilidad.");

  return partes.join("\n");
}

// Para el analizador (navegador) y para las pruebas (Node).
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    clasificarCampo,
    pareceLogin,
    campoConRol,
    elegirCookieDeSesion,
    pareceExportacion,
    formatearContrato,
    USER_HINTS,
    PASS_HINTS,
    CSRF_HINTS,
    SHEET_TYPES,
  };
}
