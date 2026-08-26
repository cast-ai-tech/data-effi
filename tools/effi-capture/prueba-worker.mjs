/**
 * Prueba de regresión del capturador: ¿sobrevive al sueño del service worker?
 *
 * POR QUÉ EXISTE ESTE ARCHIVO
 * ---------------------------
 * MV3 duerme el service worker a los 30 segundos sin actividad y al despertarlo
 * vuelve a evaluar background.js desde cero, con la memoria en blanco. Entre
 * pulsar «Empezar a grabar» y terminar de teclear una contraseña pasan de sobra
 * esos 30 segundos, así que el sueño no es el caso raro: es el caso normal.
 *
 * Durante un tiempo la extensión decidía dentro del listener, de forma síncrona,
 * si anotar una petición. Tras el sueño esa decisión leía un `grabando` recién
 * inicializado a false y descartaba la petición - justamente la del login. El
 * síntoma era una captura que terminaba en «NO SE CAPTURÓ EL LOGIN» y tres
 * explicaciones posibles, ninguna de ellas la verdadera, así que quien capturaba
 * repetía el proceso una y otra vez sin llegar a nada.
 *
 * Es un fallo que solo aparece con el reloj de por medio: abrir Chrome y probar
 * a mano no lo enseña si uno va rápido. Por eso vive aquí, donde el sueño se
 * provoca a voluntad y no hay que esperar treinta segundos para verlo.
 *
 * CÓMO EMULA A CHROME
 * -------------------
 * `chrome.storage.session` sobrevive al sueño; la memoria del script no. Dormir
 * al worker es tirar el contexto y volver a evaluar background.js contra el
 * mismo almacén, que es exactamente lo que hace Chrome.
 *
 *     node tools/effi-capture/prueba-worker.mjs
 */

import vm from "node:vm";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const EXTENSION = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "extension",
);

// Lo único que cruza el sueño del worker, igual que en Chrome.
const almacen = new Map();
const copia = (v) => JSON.parse(JSON.stringify(v));

function arrancarWorker() {
  const oyentes = { peticion: [], cabeceras: [], mensaje: [], instalado: [] };

  const chrome = {
    storage: {
      session: {
        async set(obj) {
          for (const [k, v] of Object.entries(obj)) almacen.set(k, copia(v));
        },
        async get(claves) {
          const salida = {};
          for (const k of [].concat(claves)) {
            if (almacen.has(k)) salida[k] = copia(almacen.get(k));
          }
          return salida;
        },
      },
    },
    webRequest: {
      onBeforeRequest: { addListener: (f) => oyentes.peticion.push(f) },
      onHeadersReceived: { addListener: (f) => oyentes.cabeceras.push(f) },
    },
    runtime: {
      onMessage: { addListener: (f) => oyentes.mensaje.push(f) },
      onInstalled: { addListener: (f) => oyentes.instalado.push(f) },
    },
  };

  const entorno = { chrome, console, URL, TextDecoder, setTimeout, fetch };
  entorno.globalThis = entorno;
  const ctx = vm.createContext(entorno);
  entorno.importScripts = (...ficheros) => {
    for (const fichero of ficheros) {
      vm.runInContext(
        fs.readFileSync(path.join(EXTENSION, fichero), "utf8"),
        ctx,
        { filename: fichero },
      );
    }
  };
  vm.runInContext(
    fs.readFileSync(path.join(EXTENSION, "background.js"), "utf8"),
    ctx,
    { filename: "background.js" },
  );
  return oyentes;
}

let worker = arrancarWorker();

/** Lo que pasa a los 30 segundos: el contexto muere, el almacén queda. */
const dormirWorker = () => {
  worker = arrancarWorker();
};

/** Deja correr las promesas que el worker tiene pendientes. */
const respirar = () => new Promise((r) => setTimeout(r, 0));

const pedir = (d) => worker.peticion.forEach((f) => f(d));
const responder = (d) => worker.cabeceras.forEach((f) => f(d));
const alPopup = (accion) =>
  new Promise((res) => worker.mensaje.forEach((f) => f({ accion }, null, res)));

// Un login real de Effi: POST con email, password y el CSRF de CodeIgniter, que
// responde con la cookie de sesión.
const LOGIN = {
  requestId: "42",
  method: "POST",
  url: "https://effi.com.co/ingreso/validar_usuario",
  requestBody: {
    formData: {
      email: ["ana@ejemplo.com"],
      password: ["esto-no-se-mira-nunca"],
      token: ["dbd2bde740551a2435b72661473ee906"],
    },
  },
};
const RESPUESTA_LOGIN = {
  requestId: "42",
  statusCode: 302,
  responseHeaders: [
    { name: "set-cookie", value: "ci_session=abc123; Path=/; HttpOnly" },
    { name: "content-type", value: "text/html; charset=UTF-8" },
  ],
};

async function capturar({ conSueño }) {
  almacen.clear();
  worker = arrancarWorker();

  await alPopup("empezar");
  if (conSueño) {
    await respirar();
    dormirWorker(); // la persona tarda más de 30 s en teclear su contraseña
  }
  pedir(LOGIN);
  responder(RESPUESTA_LOGIN);
  await respirar();
  await alPopup("parar");
  return alPopup("estado");
}

const casos = [];
function comprobar(nombre, condicion) {
  casos.push(Boolean(condicion));
  console.log(`  ${condicion ? "ok   " : "FALLA"}  ${nombre}`);
}

console.log("");
console.log("Captura con el worker vivo");
{
  const { contrato, total } = await capturar({ conSueño: false });
  comprobar("anota la petición", total === 1);
  comprobar("reconoce el login", contrato.ruta === "/ingreso/validar_usuario");
  comprobar(
    "nombra los campos",
    contrato.campoUsuario === "email" && contrato.campoClave === "password",
  );
  comprobar("nombra el CSRF", contrato.campoCsrf === "token");
  comprobar("nombra la cookie", contrato.carrierNombre === "ci_session");
}

console.log("");
console.log("Captura con el worker dormido en medio (el caso normal)");
{
  const { contrato, total } = await capturar({ conSueño: true });
  comprobar("el sueño no se lleva la petición", total === 1);
  comprobar("el login sigue ahí", contrato.ruta === "/ingreso/validar_usuario");
  comprobar(
    "los campos siguen ahí",
    contrato.campoUsuario === "email" && contrato.campoClave === "password",
  );
  comprobar("la cookie sigue ahí", contrato.carrierNombre === "ci_session");
}

console.log("");
console.log("El contrato que se manda no lleva ni un secreto dentro");
{
  const { texto } = await capturar({ conSueño: true });
  comprobar("no sale la contraseña", !texto.includes("esto-no-se-mira-nunca"));
  comprobar("no sale el email", !texto.includes("ana@ejemplo.com"));
  comprobar("no sale el valor de la cookie", !texto.includes("abc123"));
  comprobar(
    "no sale el valor del CSRF",
    !texto.includes("dbd2bde740551a2435b72661473ee906"),
  );
}

const pasan = casos.filter(Boolean).length;
console.log("");
console.log(`${pasan}/${casos.length} comprobaciones pasan.`);
if (pasan !== casos.length) {
  console.error("La extensión no captura lo que debe.");
  process.exit(1);
}
