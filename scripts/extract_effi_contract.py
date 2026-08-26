"""Lee un HAR de Effi y saca el contrato de login SIN sacar ningún secreto.

    python -m scripts.extract_effi_contract "C:/ruta/effi.har"
    python -m scripts.extract_effi_contract effi.har --limpiar effi-limpio.har

POR QUÉ ESTE SCRIPT EXISTE
--------------------------
Para automatizar el login de Effi hacen falta seis datos: la ruta del formulario,
cómo se llaman los campos de usuario y contraseña, si hay un token CSRF, si la
sesión vuelve como cookie o como JSON, y cómo se llama esa cookie. Todo eso está
en un HAR, que es la grabación que el navegador hace de su propio tráfico.

El problema es que un HAR **también** contiene la contraseña que se tecleó, en
texto plano, y las cookies de sesión. Pedirle a alguien "mándame el HAR" es
pedirle que mande su contraseña por WhatsApp.

Este script rompe ese trato: lee el HAR **en el computador de esa persona** e
imprime únicamente NOMBRES y ESTRUCTURA. Nunca un valor. Lo que sale por pantalla
se puede pegar en un chat sin pensarlo dos veces, y el HAR nunca sale de su
máquina.

LA REGLA QUE ESTE ARCHIVO OBEDECE
---------------------------------
Ningún valor de un campo, cookie o cabecera se imprime jamás. Solo se dice si
existe, cómo se llama, y de qué tipo parece ser. Si alguna vez hace falta ver un
valor para depurar, la respuesta correcta es mirar el HAR a mano, no aflojar esta
regla: en el momento en que este script imprime un valor, deja de ser seguro
enviarle su salida a nadie.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Cómo reconocer, por el NOMBRE del campo, para qué sirve. Cubre las formas en
# que un panel en español suele nombrar sus inputs.
USER_HINTS = ("usuario", "user", "login", "email", "correo", "documento", "cedula", "nit")
PASS_HINTS = ("clave", "pass", "password", "contrasena", "contraseña", "pwd", "secret")
CSRF_HINTS = ("csrf", "token", "authenticity", "_token", "xsrf", "nonce", "state")

# Palabras que, en una URL, delatan un endpoint de exportación de reportes.
EXPORT_HINTS = ("export", "reporte", "report", "descarga", "download", "excel", "xls", "csv")

# Tipos de contenido que son una planilla de verdad y no una página de error.
SHEET_TYPES = ("spreadsheet", "excel", "csv", "octet-stream", "vnd.ms-excel")


def _classify(name: str) -> str | None:
    """Para qué sirve un campo, deducido solo de su nombre. Nunca del valor."""
    low = name.lower()
    # CSRF primero: un campo llamado "user_token" es un token, no un usuario.
    if any(h in low for h in CSRF_HINTS):
        return "csrf"
    if any(h in low for h in PASS_HINTS):
        return "password"
    if any(h in low for h in USER_HINTS):
        return "username"
    return None


def _post_fields(entry: dict[str, Any]) -> list[dict[str, str]]:
    """Los campos de un POST, por nombre. Los valores se descartan aquí mismo."""
    post = entry.get("request", {}).get("postData") or {}
    fields: list[dict[str, str]] = []

    for param in post.get("params") or []:
        name = param.get("name", "")
        if name:
            fields.append({"name": name, "role": _classify(name) or ""})

    # Un login que manda JSON no llena `params`. Se leen las CLAVES del cuerpo,
    # nunca sus valores, y solo del primer nivel.
    if not fields and "json" in (post.get("mimeType") or ""):
        try:
            body = json.loads(post.get("text") or "{}")
            if isinstance(body, dict):
                fields.extend(
                    {"name": str(name), "role": _classify(str(name)) or ""}
                    for name in body
                )
        except (ValueError, TypeError):
            pass

    return fields


def _find_login(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """El POST que lleva a la vez un campo de usuario y uno de contraseña.

    Se busca por ESTRUCTURA y no por la ruta, porque la ruta es precisamente uno
    de los datos que no conocemos. Un POST con esos dos campos juntos es un
    login, se llame como se llame.
    """
    for entry in entries:
        if (entry.get("request", {}).get("method") or "").upper() != "POST":
            continue
        fields = _post_fields(entry)
        roles = {f["role"] for f in fields}
        if "username" in roles and "password" in roles:
            return {"entry": entry, "fields": fields}
    return None


def _session_carrier(entry: dict[str, Any]) -> tuple[str, str]:
    """Cómo vuelve la sesión: ('cookie', nombre) o ('json', clave)."""
    response = entry.get("response", {})

    # Una cookie de sesión: la que el servidor MANDA en la respuesta del login.
    candidates = []
    for cookie in response.get("cookies") or []:
        name = cookie.get("name", "")
        if name:
            candidates.append(name)
    if not candidates:
        for header in response.get("headers") or []:
            if (header.get("name") or "").lower() == "set-cookie":
                raw = header.get("value") or ""
                name = raw.split("=", 1)[0].strip()
                if name:
                    candidates.append(name)
    if candidates:
        # La que más parece de sesión; si ninguna lo parece, la primera.
        for name in candidates:
            if any(h in name.lower() for h in ("session", "sess", "auth", "sid", "token")):
                return "cookie", name
        return "cookie", candidates[0]

    # Un token en el cuerpo JSON.
    content = response.get("content") or {}
    if "json" in (content.get("mimeType") or ""):
        try:
            body = json.loads(content.get("text") or "{}")
            if isinstance(body, dict):
                for key in body:
                    if any(h in str(key).lower() for h in ("token", "jwt", "access", "session")):
                        return "json", str(key)
        except (ValueError, TypeError):
            pass

    return "", ""


def _find_exports(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Peticiones que parecen descargar un reporte. Solo ruta y parámetros."""
    from urllib.parse import urlparse

    found: list[dict[str, str]] = []
    seen: set[str] = set()

    for entry in entries:
        url = entry.get("request", {}).get("url") or ""
        parsed = urlparse(url)
        path = parsed.path
        if not path or path in seen:
            continue

        mime = ((entry.get("response") or {}).get("content") or {}).get("mimeType") or ""
        looks_like_sheet = any(t in mime.lower() for t in SHEET_TYPES)
        named_like_export = any(h in path.lower() for h in EXPORT_HINTS)
        if not (looks_like_sheet or named_like_export):
            continue

        seen.add(path)
        # Solo los NOMBRES de los parámetros: un rango de fechas no es secreto,
        # pero un id de cliente en la query sí podría serlo.
        params = [q.get("name", "") for q in entry.get("request", {}).get("queryString") or []]
        found.append({
            "path": path,
            "method": (entry.get("request", {}).get("method") or "").upper(),
            "params": ", ".join(p for p in params if p) or "(ninguno)",
            "mime": mime.split(";")[0] or "(sin tipo)",
            "status": str((entry.get("response") or {}).get("status") or ""),
        })

    return found


def _sanitise(har: dict[str, Any]) -> dict[str, Any]:
    """Una copia del HAR con todo valor sensible reemplazado por '***'.

    Para cuando alguien quiera guardar la grabación o mandarla igual. No
    reemplaza selectivamente lo que "parece" secreto: vacía TODOS los cuerpos,
    cookies y cabeceras de autorización, porque adivinar cuál de ellos era el
    importante es exactamente la clase de decisión que sale mal una vez.
    """
    for entry in har.get("log", {}).get("entries", []):
        request = entry.get("request", {})
        response = entry.get("response", {})

        for param in (request.get("postData") or {}).get("params") or []:
            param["value"] = "***"
        if (request.get("postData") or {}).get("text"):
            request["postData"]["text"] = "***"

        for cookie in (request.get("cookies") or []) + (response.get("cookies") or []):
            cookie["value"] = "***"

        for headers in (request.get("headers") or [], response.get("headers") or []):
            for header in headers:
                name = (header.get("name") or "").lower()
                if name in ("cookie", "set-cookie", "authorization", "x-csrf-token"):
                    header["value"] = "***"

        if (response.get("content") or {}).get("text"):
            response["content"]["text"] = "***"

    return har


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 1

    har_path = Path(args[0])
    if not har_path.exists():
        print(f"No encuentro el archivo: {har_path}", file=sys.stderr)
        return 1

    try:
        har = json.loads(har_path.read_text(encoding="utf-8", errors="replace"))
    except ValueError as exc:
        print(f"Ese archivo no es un HAR válido: {exc}", file=sys.stderr)
        return 1

    entries = har.get("log", {}).get("entries", [])
    print(f"\nGrabación con {len(entries)} peticiones.\n")

    login = _find_login(entries)
    if not login:
        print("NO ENCONTRÉ EL LOGIN.")
        print("  Un login es un POST que lleva juntos un campo de usuario y uno de")
        print("  contraseña. Si no aparece, casi siempre es una de estas tres:")
        print("    · La grabación empezó DESPUÉS de entrar. Cierra sesión, y")
        print("      empieza a grabar ANTES de escribir el usuario.")
        print("    · Se marcó «Preserve log» tarde y el login se borró al recargar.")
        print("    · Effi manda el login a otro dominio y se filtró por dominio.")
        return 1

    from urllib.parse import urlparse

    entry = login["entry"]
    url = entry.get("request", {}).get("url") or ""
    parsed = urlparse(url)
    fields = login["fields"]

    def field_named(role: str) -> str:
        for f in fields:
            if f["role"] == role:
                return f["name"]
        return ""

    carrier, carrier_name = _session_carrier(entry)
    status = (entry.get("response") or {}).get("status")

    print("=" * 68)
    print("CONTRATO DE LOGIN — esto es lo único que hace falta")
    print("=" * 68)
    print(f"  base            {parsed.scheme}://{parsed.netloc}")
    print(f"  ruta            {parsed.path}")
    print(f"  campo usuario   {field_named('username')}")
    print(f"  campo clave     {field_named('password')}")
    print(f"  campo CSRF      {field_named('csrf') or '(ninguno)'}")
    print(f"  sesión vuelve   {carrier or '(no se detectó)'}  {carrier_name}")
    print(f"  respondió       HTTP {status}")
    print(f"  otros campos    "
          f"{', '.join(f['name'] for f in fields if not f['role']) or '(ninguno)'}")

    if not carrier:
        print("\n  AVISO: no se detectó cómo vuelve la sesión. Puede que Effi la")
        print("  entregue en una petición posterior al login. Manda también la")
        print("  lista de exportaciones de abajo y lo miramos.")

    print("\n" + "=" * 68)
    print("PARA PEGAR EN EL .env DEL SERVIDOR")
    print("=" * 68)
    print(f"EFFI_BASE_URL={parsed.scheme}://{parsed.netloc}")
    print(f"EFFI_LOGIN_PATH={parsed.path}")
    print(f"EFFI_LOGIN_USER_FIELD={field_named('username')}")
    print(f"EFFI_LOGIN_PASS_FIELD={field_named('password')}")
    print(f"EFFI_LOGIN_CSRF_FIELD={field_named('csrf')}")
    print(f"EFFI_SESSION_CARRIER={carrier}")
    if carrier == "cookie":
        print(f"EFFI_SESSION_COOKIE={carrier_name}")
    elif carrier == "json":
        print(f"EFFI_TOKEN_JSON_KEY={carrier_name}")

    exports = _find_exports(entries)
    print("\n" + "=" * 68)
    print(f"DESCARGAS DE REPORTES ENCONTRADAS ({len(exports)})")
    print("=" * 68)
    if exports:
        for item in exports:
            print(f"  {item['method']:5} {item['path']}")
            print(f"        parámetros: {item['params']}")
            print(f"        devolvió:   HTTP {item['status']}  {item['mime']}")
    else:
        print("  Ninguna. Si querías capturarlas, entra a cada reporte y pulsa")
        print("  «Exportar» una vez por reporte, con la grabación andando.")

    print("\n" + "=" * 68)
    print("NADA DE LO DE ARRIBA ES UN SECRETO")
    print("=" * 68)
    print("  Son nombres de campos y rutas: ni una contraseña, ni una cookie, ni")
    print("  un valor. Esta salida se puede pegar en un chat con tranquilidad.")
    print("  El archivo .har SÍ tiene la contraseña dentro: NO lo mandes.")
    print("  Cuando termines, bórralo.\n")

    if "--limpiar" in sys.argv:
        idx = sys.argv.index("--limpiar")
        if idx + 1 < len(sys.argv):
            out = Path(sys.argv[idx + 1])
            out.write_text(json.dumps(_sanitise(har), indent=1), encoding="utf-8")
            print(f"  Copia sin secretos guardada en: {out}")
            print("  Esa copia sí se puede compartir. El original sigue siendo peligroso.\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
