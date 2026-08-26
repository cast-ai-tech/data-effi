"""El buzón de capturas: que llegue solo, y que no llegue de más.

QUÉ DEFIENDEN ESTAS PRUEBAS
---------------------------
El buzón acepta un POST sin sesión, desde el computador de alguien que no tiene
cuenta aquí. Eso es correcto - quien captura nos está haciendo un favor y no debe
tener que registrarse - y significa que todo lo que lo protege es código, no un
login.

Las tres capas, y lo que pasa si una se cae:

  el modelo de entrada   si acepta campos de más, una extensión modificada mete
                         una contraseña en una tabla que nadie trata como
                         sensible, y ahí se queda para siempre
  el trigger del motor   última línea; ya está probado contra Postgres real
  el veredicto de vuelta si miente, la persona cierra Effi creyendo que sirvió
                         y hay que volver a pedírselo mañana

`api/schemas.py` es lo que se prueba aquí, sin base de datos: es la capa que
decide qué llega a existir.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import CaptureSubmission

CONTRATO_BUENO = {
    "base": "https://app.effi.com.co",
    "ruta": "/auth/ingresar",
    "campoUsuario": "usuario",
    "campoClave": "clave",
    "campoCsrf": "_token",
    "carrier": "cookie",
    "carrierNombre": "effi_sesion",
    "estado": 302,
    "otrosCampos": ["recordar"],
    "exportaciones": [
        {
            "metodo": "GET",
            "ruta": "/reportes/guias/export",
            "params": "fecha_inicio, fecha_fin",
            "estado": 200,
            "tipo": "application/vnd.ms-excel",
        }
    ],
}


# =============================================================================
# Lo que se acepta
# =============================================================================


def test_un_contrato_legitimo_pasa_entero():
    """Los nombres de campo tienen que sobrevivir, incluido `campoClave`.

    Un filtro demasiado celoso que rechazara cualquier cosa con la palabra
    "clave" dentro tiraría el contrato entero: el campo se LLAMA `campoClave` y
    su contenido legítimo es el texto "clave".
    """
    envio = CaptureSubmission(**CONTRATO_BUENO)
    guardado = envio.contract_for_storage()

    assert guardado["campoClave"] == "clave"
    assert guardado["campoUsuario"] == "usuario"
    assert guardado["carrierNombre"] == "effi_sesion"
    assert len(guardado["exportaciones"]) == 1


def test_una_captura_sin_login_se_acepta_igual():
    """Sirve para decirle a la persona que repita, y para saber que lo intentó.

    Rechazarla dejaría a quien capturó sin respuesta, mirando un error, sin
    saber que lo que falló fue el orden en que hizo los pasos.
    """
    envio = CaptureSubmission(source="extension")
    guardado = envio.contract_for_storage()
    assert guardado["ruta"] == ""
    assert guardado["exportaciones"] == []


# =============================================================================
# Lo que NO se acepta. Esta es la parte que importa.
# =============================================================================


@pytest.mark.parametrize(
    "campo_extra",
    ["password", "contrasena", "cookie", "authorization", "session_value", "clave_real"],
)
def test_un_campo_de_mas_se_descarta_en_la_puerta(campo_extra):
    """Una extensión modificada no puede colar un secreto en la base.

    No lanza error, y eso es deliberado: `extra: ignore` hace que el campo
    simplemente no exista del otro lado. Rechazar con un 422 le diría a quien lo
    intenta exactamente qué nombre probar después.
    """
    envio = CaptureSubmission(**{**CONTRATO_BUENO, campo_extra: "SECRETO-QUE-NO-DEBE-PASAR"})
    guardado = envio.contract_for_storage()

    assert campo_extra not in guardado
    assert "SECRETO-QUE-NO-DEBE-PASAR" not in str(guardado)


def test_lo_guardado_es_exactamente_lo_declarado():
    """La lista de claves está fija en el código, no la decide quien envía.

    Si alguien añade un campo al modelo sin pensarlo, esta prueba falla y le
    obliga a preguntarse si ese campo puede llevar un valor de usuario.
    """
    guardado = CaptureSubmission(**CONTRATO_BUENO).contract_for_storage()

    assert set(guardado) == {
        "base", "ruta", "campoUsuario", "campoClave", "campoCsrf",
        "carrier", "carrierNombre", "estado", "otrosCampos", "exportaciones",
    }


def test_un_carrier_inventado_se_rechaza():
    """`carrier` acaba en una variable de entorno del servidor: es un enum."""
    with pytest.raises(ValidationError):
        CaptureSubmission(**{**CONTRATO_BUENO, "carrier": "loquesea"})


def test_las_listas_tienen_tope():
    """Sin tope, el buzón es un sitio donde escribir gratis y sin límite."""
    with pytest.raises(ValidationError):
        CaptureSubmission(**{**CONTRATO_BUENO, "otrosCampos": [f"c{i}" for i in range(100)]})

    with pytest.raises(ValidationError):
        CaptureSubmission(
            **{**CONTRATO_BUENO, "exportaciones": [{"ruta": f"/r{i}"} for i in range(100)]}
        )


def test_los_textos_largos_se_rechazan():
    """Una ruta de 5.000 caracteres no es una ruta."""
    with pytest.raises(ValidationError):
        CaptureSubmission(**{**CONTRATO_BUENO, "ruta": "/" + "x" * 5000})


def test_un_origen_inventado_se_rechaza():
    with pytest.raises(ValidationError):
        CaptureSubmission(**{**CONTRATO_BUENO, "source": "otra-cosa"})


# =============================================================================
# El servidor no confía en el cliente, y está escrito donde se puede comprobar
# =============================================================================


def test_el_motor_tambien_rechaza_secretos():
    """La migración 052 lleva su propio guardia, y tiene que seguir ahí.

    El modelo de arriba ya descarta los campos de más, así que este trigger es
    redundante — hasta el día en que alguien escriba en `raw.capture` desde otro
    sitio. Verificado contra Postgres real el 2026-08-25: rechaza `password`,
    `cookie`, `authorization` y `contrasena`, y acepta un contrato legítimo.
    """
    from pathlib import Path

    sql = Path("migrations/052_capture_inbox.sql").read_text(encoding="utf-8")

    assert "core.reject_capture_secrets" in sql
    assert "trg_capture_no_secrets" in sql
    for palabra in ("password", "contrasena", "secret", "cookie", "authorization"):
        assert palabra in sql, f"El trigger dejó de vigilar «{palabra}»"
    # Y el tope de tamaño, que es lo que impide usar el buzón de almacén.
    assert "16384" in sql


def test_el_endpoint_publico_limita_por_ip_antes_de_mirar_el_codigo():
    """El orden importa: primero el límite, después el código.

    Al revés, un script podría probar códigos a toda velocidad y solo se
    frenaría al acertar - que es justo cuando ya da igual.
    """
    from pathlib import Path

    fuente = Path("api/routers/captures.py").read_text(encoding="utf-8")
    cuerpo = fuente.split("def submit_capture", 1)[1]

    posicion_limite = cuerpo.find("check_rate_limit")
    posicion_token = cuerpo.find("_valid_token")

    assert posicion_limite != -1, "El endpoint público perdió su límite por IP"
    assert posicion_limite < posicion_token, (
        "El límite por IP tiene que ir ANTES de validar el código."
    )


def test_las_notificaciones_apuntan_a_rutas_que_existen():
    """Un aviso que lleva a un 404 es peor que no avisar.

    Este test existe porque pasó: la notificación decía «revisa esto» y enlazaba
    a `/conexiones`, cuando la carpeta de la web es `connections`. El clic moría
    en un 404 y quien lo recibía no tenía forma de saber qué mirar.

    Comprueba los deep_link que escriben los avisos de este flujo contra las
    carpetas reales de web/app.
    """
    from pathlib import Path

    rutas_web = {p.name for p in Path("web/app").iterdir() if p.is_dir()}

    fuentes = [Path("api/routers/captures.py"), Path("worker/jobs.py")]
    enlaces: set[str] = set()
    for fuente in fuentes:
        texto = fuente.read_text(encoding="utf-8")
        for linea in texto.splitlines():
            if '"deep_link"' in linea and '"/' in linea:
                enlace = linea.split('"deep_link"')[1].split('"')[1]
                # Solo los estáticos de primer nivel; los que llevan país se
                # arman en tiempo de ejecución y no se pueden comprobar así.
                if enlace.count("/") == 1 and enlace != "/":
                    enlaces.add(enlace)

    assert enlaces, "Se dejaron de encontrar deep_links: revisa este test"
    for enlace in enlaces:
        assert enlace.lstrip("/") in rutas_web, (
            f"El aviso enlaza a «{enlace}» y esa página no existe en web/app. "
            f"Carpetas reales: {sorted(rutas_web)}"
        )


def test_una_captura_no_se_aplica_sola():
    """Recibir no es configurar. Un POST de fuera no puede tocar el .env.

    Si alguna vez este endpoint empezara a escribir en core.connection o en las
    variables del conector, un desconocido con un código podría redirigir a
    dónde entramos con la cuenta de un comerciante.
    """
    from pathlib import Path

    fuente = Path("api/routers/captures.py").read_text(encoding="utf-8")
    cuerpo = fuente.split("def submit_capture", 1)[1].split("\ndef ", 1)[0]

    for prohibido in ("core.connection", "connection_credential", "LOGIN_CONTRACT"):
        assert prohibido not in cuerpo, (
            f"submit_capture toca «{prohibido}». Recibir una captura debe guardar "
            "y avisar, nunca aplicar nada."
        )
