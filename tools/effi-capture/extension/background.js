/**
 * El capturador. Escucha el tráfico hacia Effi y anota SOLO la forma.
 *
 * POR QUÉ UNA EXTENSIÓN Y NO "abre DevTools y guarda un HAR"
 * ---------------------------------------------------------
 * El HAR funciona y tiene dos problemas que importan cuando alguien va a repetir
 * esto varias veces:
 *
 *   1. Se hace mal a la primera. Hay que abrir DevTools ANTES de entrar, marcar
 *      "Preserve log" ANTES de que la página navegue, y no cerrar el panel. Casi
 *      todo el mundo se salta uno de los tres y la grabación sale vacía - y solo
 *      se entera al final, cuando ya cerró sesión.
 *   2. El archivo lleva la contraseña dentro. Pedirlo por chat es pedir una
 *      contraseña por chat.
 *
 * Esto arregla las dos: un botón para grabar, y lo que sale ya viene sin
 * secretos porque nunca se guardaron.
 *
 * QUÉ SE GUARDA Y QUÉ NO
 * ----------------------
 *   SE GUARDA   la ruta, el método, los NOMBRES de los campos, el HTTP de vuelta,
 *               el NOMBRE de la cookie de sesión, el tipo de contenido.
 *   NO SE TOCA  el valor de ningún campo, ninguna cookie, ningún encabezado, y
 *               ningún cuerpo de respuesta.
 *
 * `chrome.webRequest` entrega el cuerpo del formulario en `requestBody.formData`,
 * o sea que la contraseña pasa literalmente por este archivo en cada captura. Lo
 * único que se hace con ella es `Object.keys(...)`. Ese es el archivo entero.
 *
 * DÓNDE MIRA
 * ----------
 * Solo en los dominios de `host_permissions` del manifest: *.effi.com.co y
 * *.efficommerce.com. No en el resto de internet. Es deliberado y es
 * comprobable: Chrome lo muestra al instalar, y una extensión que pidiera
 * <all_urls> para esto no tendría por qué.
 */

/* eslint-env webextensions */

importScripts("config.js", "contrato.js");

// Lo capturado en la sesión actual. Vive en memoria del service worker y se
// vuelca a chrome.storage.session para que el popup lo lea aunque el worker se
// haya dormido - que en MV3 pasa a los 30 segundos.
let grabando = false;
let peticiones = [];

const MAX_PETICIONES = 400; // un tope sano: una captura normal no llega a 100

// -- estado, persistido para sobrevivir al sueño del service worker ---------

async function guardarEstado() {
  await chrome.storage.session.set({ grabando, peticiones });
}

async function cargarEstado() {
  const guardado = await chrome.storage.session.get(["grabando", "peticiones"]);
  grabando = guardado.grabando || false;
  peticiones = guardado.peticiones || [];
}

// -- captura ---------------------------------------------------------------

/**
 * Nombres de los campos de un envío. Aquí es donde pasa la contraseña, y aquí
 * es donde se queda: se leen las claves y el objeto se descarta.
 */
function nombresDeCampos(requestBody) {
  if (!requestBody) return [];

  if (requestBody.formData) {
    // Object.keys y nada más. Los valores están en requestBody.formData[k] y
    // este archivo no los mira nunca.
    return Object.keys(requestBody.formData);
  }

  // Un login que manda JSON llega como bytes crudos. Se decodifica solo para
  // sacar las CLAVES del primer nivel, y el texto se descarta enseguida.
  if (requestBody.raw && requestBody.raw.length) {
    try {
      const bytes = requestBody.raw[0].bytes;
      if (!bytes) return [];
      const texto = new TextDecoder("utf-8").decode(bytes);
      const cuerpo = JSON.parse(texto);
      return cuerpo && typeof cuerpo === "object" ? Object.keys(cuerpo) : [];
    } catch {
      return [];
    }
  }

  return [];
}

chrome.webRequest.onBeforeRequest.addListener(
  (detalles) => {
    if (!grabando || peticiones.length >= MAX_PETICIONES) return;

    const url = new URL(detalles.url);
    const campos = nombresDeCampos(detalles.requestBody).map((name) => ({
      name,
      role: clasificarCampo(name),
    }));

    peticiones.push({
      id: detalles.requestId,
      metodo: detalles.method,
      base: `${url.protocol}//${url.host}`,
      ruta: url.pathname,
      params: [...url.searchParams.keys()], // nombres, no valores
      campos,
      estado: null,
      tipo: "",
      cookies: [],
    });
    guardarEstado();
  },
  { urls: ["*://*.effi.com.co/*", "*://*.efficommerce.com/*"] },
  ["requestBody"],
);

chrome.webRequest.onHeadersReceived.addListener(
  (detalles) => {
    if (!grabando) return;
    const registro = peticiones.find((p) => p.id === detalles.requestId);
    if (!registro) return;

    registro.estado = detalles.statusCode;

    for (const cabecera of detalles.responseHeaders || []) {
      const nombre = (cabecera.name || "").toLowerCase();
      if (nombre === "content-type") {
        // El tipo, sin el charset. No es un secreto.
        registro.tipo = String(cabecera.value || "")
          .split(";")[0]
          .trim();
      }
      if (nombre === "set-cookie") {
        // SOLO el nombre, lo de antes del "=". El valor es la sesión y se tira.
        const nombreCookie = String(cabecera.value || "")
          .split("=")[0]
          .trim();
        if (nombreCookie && !registro.cookies.includes(nombreCookie)) {
          registro.cookies.push(nombreCookie);
        }
      }
    }
    guardarEstado();
  },
  { urls: ["*://*.effi.com.co/*", "*://*.efficommerce.com/*"] },
  ["responseHeaders", "extraHeaders"],
);

// -- lo que el popup pregunta ----------------------------------------------

/** Junta todo lo capturado en el contrato que la persona va a copiar. */
function construirContrato() {
  const login = peticiones.find(
    (p) => p.metodo === "POST" && pareceLogin(p.campos),
  );

  const exportaciones = [];
  const rutasVistas = new Set();
  for (const p of peticiones) {
    if (rutasVistas.has(p.ruta)) continue;
    if (!pareceExportacion(p.ruta, p.tipo)) continue;
    rutasVistas.add(p.ruta);
    exportaciones.push({
      metodo: p.metodo,
      ruta: p.ruta,
      params: p.params.join(", "),
      estado: p.estado,
      tipo: p.tipo,
    });
  }

  if (!login) {
    return { ruta: "", exportaciones, totalPeticiones: peticiones.length };
  }

  // La sesión: normalmente la cookie que llega en la respuesta del propio login.
  let carrier = "";
  let carrierNombre = "";
  if (login.cookies.length) {
    carrier = "cookie";
    carrierNombre = elegirCookieDeSesion(login.cookies);
  } else if ((login.tipo || "").includes("json")) {
    // El token viaja en el cuerpo, que esta extensión no lee a propósito.
    carrier = "json";
    carrierNombre = "";
  }

  return {
    base: login.base,
    ruta: login.ruta,
    campoUsuario: campoConRol(login.campos, "username"),
    campoClave: campoConRol(login.campos, "password"),
    campoCsrf: campoConRol(login.campos, "csrf"),
    carrier,
    carrierNombre,
    estado: login.estado,
    otrosCampos: login.campos.filter((c) => !c.role).map((c) => c.name),
    exportaciones,
    totalPeticiones: peticiones.length,
  };
}

chrome.runtime.onMessage.addListener((mensaje, _remitente, responder) => {
  (async () => {
    await cargarEstado();

    if (mensaje.accion === "empezar") {
      grabando = true;
      peticiones = []; // cada captura empieza limpia
      await guardarEstado();
      responder({ grabando: true, total: 0 });
      return;
    }

    if (mensaje.accion === "parar") {
      grabando = false;
      await guardarEstado();
      responder({ grabando: false, total: peticiones.length });
      return;
    }

    if (mensaje.accion === "enviar") {
      // El envío vive aquí y no en el popup a propósito: el popup se cierra en
      // cuanto la persona hace clic fuera, y un fetch a medias moriría con él.
      const contrato = construirContrato();
      try {
        const respuesta = await fetch(ENVIO.url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...contrato, source: "extension" }),
        });
        const cuerpo = await respuesta.json().catch(() => ({}));
        if (!respuesta.ok) {
          responder({
            enviado: false,
            // El mensaje del servidor está escrito para esta persona; se
            // prefiere al genérico siempre que venga.
            error:
              (cuerpo.error && cuerpo.error.message) ||
              `El servidor respondió ${respuesta.status}.`,
          });
          return;
        }
        responder({ enviado: true, mensaje: cuerpo.message || "Enviado." });
      } catch (error) {
        // Sin internet, o el servidor caído. No es culpa de quien capturó y no
        // debe perder el trabajo: el texto sigue ahí para copiarlo a mano.
        responder({
          enviado: false,
          error:
            "No se pudo conectar para enviarlo (" +
            (error && error.name ? error.name : "sin red") +
            "). Copia el texto y mándalo por chat.",
        });
      }
      return;
    }

    if (mensaje.accion === "estado") {
      const contrato = construirContrato();
      responder({
        grabando,
        total: peticiones.length,
        contrato,
        texto: peticiones.length ? formatearContrato(contrato) : "",
        // Si el paquete no trae código configurado, el popup enseña el modo
        // manual en vez de un botón de enviar que no llevaría a ninguna parte.
        puedeEnviar: Boolean(ENVIO && ENVIO.url),
      });
      return;
    }

    responder({ error: "acción desconocida" });
  })();

  return true; // la respuesta es asíncrona
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.session.set({ grabando: false, peticiones: [] });
});
