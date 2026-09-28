// La ventanita. Tres cosas: ver si hay sesión de Effi, recibir el código y
// enviar. Lo que NO hace:
//   - no lee formularios ni contraseñas (no tiene permiso sobre páginas);
//   - no guarda la cookie: vive en variables locales el tiempo del envío;
//   - no guarda el código;
//   - no manda nada a ningún sitio que no sea Master Data.
import { CONFIG } from "./config.js";
import {
  buildPayload,
  describeResponse,
  normalizeCode,
  normalizeOrigin,
  pickSessionCookies,
} from "./lib.js";

const $ = (id) => document.getElementById(id);

const estadoEffi = $("estado-effi");
const formulario = $("formulario");
const codigo = $("codigo");
const enviar = $("enviar");
const resultado = $("resultado");
const servidor = $("servidor");

let haySesion = false;
let enviando = false;

function pintar(el, tono, texto) {
  el.dataset.tono = tono;
  el.textContent = texto;
  el.hidden = false;
}

async function apiBase() {
  try {
    const guardado = await chrome.storage.local.get("apiBase");
    return normalizeOrigin(guardado.apiBase) || CONFIG.apiBase;
  } catch {
    return CONFIG.apiBase;
  }
}

async function cookiesDeSesion() {
  const todas = [];
  for (const name of CONFIG.sessionCookies) {
    todas.push(...(await chrome.cookies.getAll({ domain: CONFIG.effiDomain, name })));
  }
  return pickSessionCookies(todas, CONFIG.sessionCookies, CONFIG.effiDomain);
}

async function revisarEffi() {
  try {
    const cookies = await cookiesDeSesion();
    haySesion = cookies.some((c) => c.name === CONFIG.sessionCookies[0]);
  } catch {
    haySesion = false;
  }

  if (haySesion) {
    // CodeIgniter crea `ci_session` también a quien solo abrió la página de
    // entrar. Por eso no se afirma "estás dentro": eso lo comprueba Master Data.
    pintar(
      estadoEffi,
      "ok",
      "Hay una sesión de Effi en este navegador. Asegúrate de haber entrado a tu panel antes de enviar.",
    );
  } else {
    estadoEffi.dataset.tono = "aviso";
    estadoEffi.textContent = "No encuentro una sesión de Effi en este navegador. ";
    const enlace = document.createElement("a");
    enlace.href = CONFIG.effiLoginUrl;
    enlace.target = "_blank";
    enlace.rel = "noopener noreferrer";
    enlace.textContent = "Entra a Effi";
    estadoEffi.append(enlace, " y vuelve a abrir esta ventanita.");
  }
  actualizarBoton();
}

function actualizarBoton() {
  enviar.disabled = enviando || !haySesion || normalizeCode(codigo.value) === null;
}

async function enviarSesion(event) {
  event.preventDefault();
  const code = normalizeCode(codigo.value);
  if (!code || !haySesion || enviando) return;

  enviando = true;
  enviar.textContent = "Enviando y comprobando con Effi…";
  actualizarBoton();
  resultado.hidden = true;

  let payload = null;
  try {
    const cookies = await cookiesDeSesion();
    if (!cookies.length) {
      haySesion = false;
      pintar(resultado, "error", "La sesión de Effi desapareció. Entra de nuevo a Effi.");
      return;
    }
    payload = buildPayload(code, cookies, navigator.userAgent);
    const base = await apiBase();
    const respuesta = await fetch(`${base}${CONFIG.redeemPath}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      // Ninguna cookie nuestra ni de nadie viaja con esta petición: la sesión
      // de Effi va en el cuerpo, una vez, y solo a Master Data.
      credentials: "omit",
      cache: "no-store",
    });
    let cuerpo = null;
    try {
      cuerpo = await respuesta.json();
    } catch {
      cuerpo = null;
    }
    const { tono, texto } = describeResponse(respuesta.status, cuerpo);
    pintar(resultado, tono, texto);
    if (tono === "ok") codigo.value = "";
  } catch {
    pintar(
      resultado,
      "error",
      "No se pudo contactar a Master Data. Revisa tu internet e inténtalo de nuevo; el código sigue sirviendo si no llegó.",
    );
  } finally {
    // La cookie no se queda en memoria de la ventanita más de lo necesario.
    payload = null;
    enviando = false;
    enviar.textContent = "Enviar sesión a Master Data";
    actualizarBoton();
  }
}

async function guardarServidor() {
  const origen = normalizeOrigin(servidor.value);
  if (!origen) {
    pintar(resultado, "error", "Esa dirección no es válida. Debe empezar por https://");
    return;
  }
  try {
    const concedido = await chrome.permissions.request({ origins: [`${origen}/*`] });
    if (!concedido) {
      pintar(resultado, "aviso", "Sin permiso para ese servidor no se puede enviar nada allí.");
      return;
    }
    await chrome.storage.local.set({ apiBase: origen });
    pintar(resultado, "ok", `Se enviará a ${origen}.`);
  } catch {
    pintar(resultado, "error", "No se pudo guardar el servidor.");
  }
}

codigo.addEventListener("input", actualizarBoton);
formulario.addEventListener("submit", (event) => void enviarSesion(event));
$("guardar-servidor").addEventListener("click", () => void guardarServidor());

void (async () => {
  servidor.value = await apiBase();
  await revisarEffi();
  codigo.focus();
})();
