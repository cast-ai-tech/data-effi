/**
 * A dónde manda la extensión la captura, y con qué código.
 *
 * ESTE ARCHIVO LO ESCRIBE EL EMPAQUETADOR, no una persona. Se genera con:
 *
 *     python -m scripts.empaquetar_captura --codigo <código> --url <url>
 *
 * El código se saca de la app: Conexiones → «Invitar a capturar». Caduca en dos
 * semanas y tiene usos contados, así que un .zip viejo olvidado en la carpeta
 * de descargas de alguien deja de servir solo.
 *
 * SI ESTO QUEDA VACÍO la extensión no se rompe: cae al modo manual, muestra el
 * texto y la persona lo copia. Es el comportamiento de antes, y sigue siendo un
 * final válido - un paquete sin código configurado tiene que servir igual, no
 * dejar a nadie mirando un botón que no hace nada.
 */

const ENVIO = {
  // Ejemplo: "https://api.midominio.com/captures/AbC123..."
  url: "",
};
