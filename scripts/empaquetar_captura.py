"""Deja listo el .zip que se le envía a quien tiene la cuenta de Effi.

    python -m scripts.empaquetar_captura
    python -m scripts.empaquetar_captura --url https://api.tudominio.com/captures/CODIGO

Con `--url`, la extensión manda la captura sola y a quien la recibe le llega una
notificación en la app. Sin `--url`, el paquete sigue funcionando: la persona
copia el texto y lo pega en un chat. Las dos formas son válidas; la primera
ahorra el paso que más se pierde.

El código se saca de la app (Conexiones → «Invitar a capturar») o de la API:

    POST /captures/tokens?label=Juan, de Distrilatam

Caduca en dos semanas y tiene usos contados, así que un .zip olvidado en la
carpeta de descargas de alguien deja de servir solo.

POR QUÉ UN SCRIPT Y NO "comprime la carpeta a mano"
---------------------------------------------------
Tres razones, y la tercera es la que de verdad importa.

La primera: la carpeta lleva un README interno con notas para nosotros que no
tiene por qué viajar. Comprimir a mano lo incluye siempre.

La segunda: con `--url` hay que escribir `config.js` Y añadir el host a
`host_permissions` del manifest. Si se hace solo lo primero, Chrome bloquea el
envío por CORS y la extensión falla justo en el último paso, en el computador de
otra persona, sin que nadie sepa por qué.

La tercera: este script COMPRUEBA antes de empaquetar. Un .zip al que le falte
`contrato.js` produce una extensión que Chrome carga y que se rompe al pulsar el
botón - y quien lo sufre es la persona que nos está haciendo un favor.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

RAIZ = Path(__file__).resolve().parent.parent
ORIGEN = RAIZ / "tools" / "effi-capture"
DESTINO = RAIZ / "captura-effi.zip"

# Lo que la persona necesita. El README interno NO está aquí a propósito.
CONTENIDO = [
    "INSTRUCCIONES.md",
    "analizador.html",
    "extension/manifest.json",
    "extension/background.js",
    "extension/config.js",
    "extension/contrato.js",
    "extension/popup.html",
    "extension/popup.js",
]

# Palabras que no deberían viajar dentro del paquete. Si aparecen, alguien dejó
# una credencial de prueba pegada en un archivo.
SOSPECHOSAS = ("password=", "clave=", "secret=", "BEGIN PRIVATE KEY", "@gmail.com")


def _config_js(url: str) -> str:
    """El archivo que le dice a la extensión a dónde enviar."""
    return f'''/**
 * A dónde manda la extensión la captura. Lo escribió el empaquetador:
 *
 *     python -m scripts.empaquetar_captura --url <url>
 *
 * El código va dentro de la URL, caduca, y tiene usos contados. Si queda vacío,
 * la extensión cae al modo manual y la persona copia el texto - que es un final
 * perfectamente válido, no un error.
 */

const ENVIO = {{
  url: {json.dumps(url)},
}};
'''


def main() -> int:
    parser = argparse.ArgumentParser(description="Arma el .zip de captura de Effi")
    parser.add_argument(
        "--url",
        default="",
        help="URL completa de envío, con el código dentro "
             "(https://api.tudominio.com/captures/CODIGO). Sin esto, el paquete "
             "funciona en modo manual.",
    )
    args = parser.parse_args()

    if not ORIGEN.exists():
        print(f"No encuentro {ORIGEN}", file=sys.stderr)
        return 1

    faltan = [nombre for nombre in CONTENIDO if not (ORIGEN / nombre).exists()]
    if faltan:
        print("Falta esto y el paquete no serviría:", file=sys.stderr)
        for nombre in faltan:
            print(f"  · {nombre}", file=sys.stderr)
        return 1

    # -- la URL, si la hay -------------------------------------------------
    host_envio = ""
    if args.url:
        partes = urlparse(args.url)
        if partes.scheme not in ("http", "https") or not partes.netloc:
            print(
                f"Esa URL no sirve: {args.url}\n"
                "  Tiene que ser completa, con https:// y el código dentro:\n"
                "  https://api.tudominio.com/captures/AbC123...",
                file=sys.stderr,
            )
            return 1
        if "/captures/" not in partes.path:
            print(
                "  AVISO: la URL no contiene «/captures/». Comprueba que sea la "
                "que devolvió POST /captures/tokens.",
                file=sys.stderr,
            )
        if partes.scheme == "http" and partes.hostname not in ("localhost", "127.0.0.1"):
            print(
                "  AVISO: es http:// y no https://. La captura no lleva secretos, "
                "pero el código sí viaja en la URL. Usa https si puedes.",
                file=sys.stderr,
            )
        host_envio = f"{partes.scheme}://{partes.netloc}/*"

    # -- nada de secretos pegados por accidente ----------------------------
    sospechas: list[str] = []
    for nombre in CONTENIDO:
        texto = (ORIGEN / nombre).read_text(encoding="utf-8", errors="replace")
        sospechas.extend(
            f"{nombre}: contiene «{palabra}»"
            for palabra in SOSPECHOSAS
            if palabra in texto
        )
    if sospechas:
        print("REVISA ESTO ANTES DE ENVIAR:", file=sys.stderr)
        for aviso in sospechas:
            print(f"  · {aviso}", file=sys.stderr)
        print("\n  Si es texto de ejemplo, ignóralo. Si es una credencial real,", file=sys.stderr)
        print("  quítala: este .zip está a punto de salir de tu computador.", file=sys.stderr)
        return 1

    # -- armar, en una carpeta temporal para no tocar el repo --------------
    if DESTINO.exists():
        DESTINO.unlink()

    with tempfile.TemporaryDirectory() as tmp:
        etapa = Path(tmp)
        for nombre in CONTENIDO:
            destino_archivo = etapa / nombre
            destino_archivo.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ORIGEN / nombre, destino_archivo)

        if args.url:
            (etapa / "extension/config.js").write_text(_config_js(args.url), encoding="utf-8")

            # Y el host, o Chrome bloquea el envío por CORS. Las dos cosas van
            # juntas siempre: un config.js con URL y un manifest sin el host es
            # una extensión que falla en el último paso.
            manifest_path = etapa / "extension/manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if host_envio not in manifest["host_permissions"]:
                manifest["host_permissions"].append(host_envio)
            manifest_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )

        with zipfile.ZipFile(DESTINO, "w", zipfile.ZIP_DEFLATED) as zf:
            for nombre in CONTENIDO:
                zf.write(etapa / nombre, arcname=f"captura-effi/{nombre}")

    tamano = DESTINO.stat().st_size / 1024
    print(f"\n  Listo: {DESTINO}  ({tamano:.0f} KB)")

    if args.url:
        print(f"  Envío automático: {args.url}")
        print(f"  Host declarado en el manifest: {host_envio}\n")
        print("  Qué decirle a quien lo reciba:")
        print("    1. Descomprime la carpeta y guárdala.")
        print("    2. Abre INSTRUCCIONES.md y sigue los pasos: son 10 minutos.")
        print("    3. Al final pulsa «Enviar automáticamente». Ya está: te llega")
        print("       una notificación en la app y no tiene que mandarte nada.\n")
    else:
        print("  Modo manual: la persona copiará el texto y te lo pegará.\n")
        print("  Para que llegue solo, genera un código y vuelve a empaquetar:")
        print("    POST /captures/tokens?label=Nombre de la persona")
        print("    python -m scripts.empaquetar_captura --url <la url que devuelve>\n")

    print("  El paquete no lleva nada sensible. Se puede mandar por donde sea.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
