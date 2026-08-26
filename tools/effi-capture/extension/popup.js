/**
 * La ventanita. Tres botones y un veredicto que dice si sirvió o no.
 *
 * POR QUÉ EL VEREDICTO IMPORTA MÁS QUE EL RESULTADO
 * -------------------------------------------------
 * Quien hace esto no sabe qué es un contrato de login ni tiene por qué. Lo único
 * que necesita saber es si le salió bien o si tiene que repetirlo - y tiene que
 * saberlo AHORA, con Effi todavía abierto, no cuando alguien le responda dos
 * horas después que la captura vino vacía.
 *
 * Por eso lo primero que se ve al terminar es una frase en verde o en ámbar
 * diciendo exactamente eso, y el texto técnico va debajo.
 */

/* eslint-env browser, webextensions */

const $ = (id) => document.getElementById(id);

function pintar(estado) {
  const grabando = estado.grabando;
  const total = estado.total || 0;
  const contrato = estado.contrato || {};
  const hayAlgo = !grabando && total > 0;

  $("punto").className =
    "punto" + (grabando ? " vivo" : hayAlgo ? " listo" : "");
  $("estadoTxt").textContent = grabando
    ? "Grabando…"
    : hayAlgo
      ? "Listo"
      : "Sin grabar";
  $("contador").textContent = total ? `${total} peticiones` : "";

  $("empezar").classList.toggle("oculto", grabando || hayAlgo);
  $("parar").classList.toggle("oculto", !grabando);
  $("instrucciones").classList.toggle("oculto", grabando || hayAlgo);
  $("resultado").classList.toggle("oculto", !hayAlgo);

  if (!hayAlgo) return;

  $("salida").textContent = estado.texto || "";

  // El botón de enviar solo aparece si el paquete trae un código configurado.
  // Un paquete sin código sigue funcionando: enseña el texto y se copia a mano,
  // que es como funcionaba antes y es un final perfectamente válido.
  $("enviar").classList.toggle("oculto", !estado.puedeEnviar);
  $("copiar").textContent = estado.puedeEnviar
    ? "O copiar y mandarlo por chat"
    : "Copiar para enviar";

  // El veredicto, en el orden en que importa: sin login no hay nada que hacer,
  // así que eso se dice primero aunque las descargas hayan salido bien.
  const caja = $("veredicto");
  const exps = (contrato.exportaciones || []).length;

  if (!contrato.ruta) {
    caja.className = "caja aviso";
    caja.textContent =
      "No se capturó el momento de entrar. Cierra sesión en Effi y graba otra " +
      "vez, empezando ANTES de escribir tu usuario.";
    return;
  }

  if (!contrato.campoClave || !contrato.campoUsuario) {
    caja.className = "caja aviso";
    caja.textContent =
      "Se vio el envío del login pero no se reconocieron sus campos. Envíalo " +
      "igual: con esto ya se puede trabajar.";
    return;
  }

  caja.className = "caja ok";
  caja.textContent =
    exps > 0
      ? `Salió bien. Se capturó la entrada y ${exps} ${exps === 1 ? "descarga" : "descargas"} de reportes.`
      : "Se capturó la entrada. No se vio ninguna descarga: si puedes, graba " +
        "otra vez exportando además Guías, Novedades y Trazabilidad de dinero.";
}

async function preguntar(accion) {
  const respuesta = await chrome.runtime.sendMessage({ accion });
  // Tras empezar o parar hace falta el estado completo, que trae el texto ya
  // formateado; la respuesta de esas dos acciones solo confirma el cambio.
  if (accion !== "estado") {
    pintar(await chrome.runtime.sendMessage({ accion: "estado" }));
  } else {
    pintar(respuesta);
  }
}

$("empezar").addEventListener("click", () => preguntar("empezar"));
$("parar").addEventListener("click", () => preguntar("parar"));
$("otra").addEventListener("click", () => preguntar("empezar"));

$("enviar").addEventListener("click", async () => {
  const boton = $("enviar");
  boton.disabled = true;
  boton.textContent = "Enviando…";

  const respuesta = await chrome.runtime.sendMessage({ accion: "enviar" });
  const aviso = $("enviado");
  aviso.classList.remove("oculto");

  if (respuesta && respuesta.enviado) {
    aviso.className = "caja ok";
    // El mensaje viene del servidor y está escrito para esta persona: dice si
    // la captura sirvió o si le faltó algo, mientras todavía tiene Effi abierto.
    aviso.textContent = respuesta.mensaje || "Enviado.";
    boton.textContent = "Enviado";
    return;
  }

  // Un fallo de envío no puede perder el trabajo: el texto sigue debajo y el
  // botón de copiar sigue ahí. Se dice exactamente eso.
  aviso.className = "caja aviso";
  aviso.textContent =
    (respuesta && respuesta.error) ||
    "No se pudo enviar. Copia el texto de abajo y mándalo por chat.";
  boton.disabled = false;
  boton.textContent = "Reintentar el envío";
});

$("copiar").addEventListener("click", async () => {
  const boton = $("copiar");
  try {
    await navigator.clipboard.writeText($("salida").textContent || "");
    boton.textContent = "Copiado — pégalo en el chat";
    setTimeout(() => (boton.textContent = "Copiar para enviar"), 2500);
  } catch {
    // El portapapeles puede estar bloqueado. Seleccionar el texto deja a la
    // persona copiarlo a mano, que es peor pero no es un callejón sin salida.
    const rango = document.createRange();
    rango.selectNodeContents($("salida"));
    const seleccion = window.getSelection();
    seleccion.removeAllRanges();
    seleccion.addRange(rango);
    boton.textContent = "Seleccionado — copia con Ctrl+C";
  }
});

// Al abrir la ventana, mostrar lo que haya: la grabación sigue viva aunque el
// popup se cierre, que es justo lo que permite entrar a Effi mientras tanto.
preguntar("estado");
