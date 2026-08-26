"""El operador de la plataforma, y sobre todo lo que NO puede ver.

POR QUÉ ESTAS PRUEBAS SON DE PRIVACIDAD Y NO DE FUNCIONALIDAD
------------------------------------------------------------
`is_platform_admin` es el primer rol que cruza organizaciones. La promesa que se
hizo al crearlo es concreta: ve cómo va el negocio de la plataforma - quién
existe, en qué plan está, qué se rompió - y **no ve los datos de ningún
comerciante**.

Esa promesa no la sostiene la buena intención de quien escriba el próximo
endpoint. La sostienen tres cosas, y aquí se comprueban las tres:

  la vista SQL      `mart.v_platform_orgs` no selecciona una sola columna de
                    core.shipment, core.movement ni nada con dinero dentro
  el modelo         `PlatformOrgRow` no tiene dónde meter una cifra de negocio
  las políticas     migración 053 no toca ninguna RLS de datos de comerciante

Si alguien añade "ventas por organización" a esta pantalla, tendrá que romper
las tres a propósito. Eso es exactamente lo que se busca.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.schemas import PlatformOrgRow

# Palabras que delatan un dato de negocio de un comerciante. Si aparecen en la
# vista o en el modelo, la promesa se rompió.
PALABRAS_DE_NEGOCIO = (
    "shipment", "guia", "guía", "movement", "movimiento",
    "revenue", "venta", "sale", "amount", "valor", "recaudo",
    "customer", "comprador", "cliente_final", "phone", "telefono",
    "profit", "margen", "cost", "flete",
)


def _sql_053() -> str:
    return Path("migrations/053_platform_admin.sql").read_text(encoding="utf-8")


def _sql_053_ejecutable() -> str:
    """La 053 sin comentarios: solo lo que Postgres llega a ejecutar.

    Los comentarios de esa migración nombran `core.shipment` precisamente para
    decir que NO la toca. Buscar ahí daría la alarma al revés: fallaría por la
    frase que promete lo que la prueba quiere comprobar.
    """
    lineas = []
    for linea in _sql_053().splitlines():
        codigo = linea.split("--", 1)[0]
        if codigo.strip():
            lineas.append(codigo)
    return "\n".join(lineas)


# =============================================================================
# Lo que el rol NO abre
# =============================================================================


def test_la_vista_de_organizaciones_no_expone_cifras_de_negocio():
    """Comprobado sobre el SQL, que es lo que la base ejecuta de verdad.

    Verificado también contra Postgres real el 2026-08-26: las trece columnas de
    `mart.v_platform_orgs` son nombre, plan, conteos y salud de conexiones.
    """
    sql = _sql_053_ejecutable()
    vista = sql.split("CREATE OR REPLACE VIEW mart.v_platform_orgs", 1)[1]
    vista = vista.split("COMMENT ON VIEW", 1)[0].lower()

    for palabra in PALABRAS_DE_NEGOCIO:
        assert palabra not in vista, (
            f"mart.v_platform_orgs menciona «{palabra}». Esa vista la lee quien "
            "opera la plataforma, no el dueño de esos datos."
        )


def test_el_modelo_no_tiene_donde_meter_una_cifra_de_negocio():
    """El contrato, en el borde de la API. Un campo nuevo aquí falla la prueba."""
    campos = set(PlatformOrgRow.model_fields)

    assert campos == {
        "org_id", "org_name", "slug", "created_at", "subscription_status",
        "plan_code", "plan_name", "trial_ends_at", "current_period_end",
        "tenant_count", "user_count", "connection_count", "connection_errors",
    }
    for campo in campos:
        for palabra in PALABRAS_DE_NEGOCIO:
            assert palabra not in campo.lower(), (
                f"PlatformOrgRow tiene «{campo}», que suena a dato de comerciante."
            )


def test_la_migracion_no_toca_la_rls_de_los_datos_de_comerciante():
    """El rol no aparece en ninguna política de las tablas con datos reales.

    Es lo que hace que la promesa sea del motor y no de la aplicación: aunque un
    guardia de la API dejara pasar a quien no debe, Postgres no le entrega una
    guía.
    """
    sql = _sql_053_ejecutable().lower()

    for tabla in ("core.shipment", "core.movement", "core.cs_interaction", "core.ad_spend"):
        assert tabla not in sql, (
            f"La migración 053 menciona {tabla}. Este rol no debe tener nada que "
            "ver con los datos de un comerciante."
        )
    # Y no crea ninguna política nueva que lo mencione.
    assert "is_platform_admin" not in sql.split("create policy")[-1] or "create policy" not in sql


# =============================================================================
# Cómo se concede: solo desde el servidor
# =============================================================================


def test_ningun_endpoint_concede_el_rol():
    """No hay camino desde la web para volverse operador.

    Es lo único que hace seguro un rol que cruza organizaciones: para tenerlo
    hay que tener acceso al servidor, y esa frontera ya existía. Una pantalla de
    "hacer admin a alguien" abriría justo la puerta que esto mantiene cerrada.
    """
    for archivo in Path("api").rglob("*.py"):
        texto = archivo.read_text(encoding="utf-8")
        # Leerlo está bien (la dependencia lo consulta). Escribirlo, no.
        assert "SET is_platform_admin" not in texto, (
            f"{archivo} escribe is_platform_admin. Solo debe hacerlo "
            "scripts/grant_platform_admin.py."
        )
        assert "is_platform_admin =" not in texto.replace("is_platform_admin ==", ""), (
            f"{archivo} asigna is_platform_admin."
        )


def test_el_script_no_crea_cuentas():
    """Una contraseña puesta por un script es una contraseña que nadie eligió.

    Y acaba en el historial de la terminal, donde se queda.
    """
    texto = Path("scripts/grant_platform_admin.py").read_text(encoding="utf-8")

    assert "INSERT INTO core.app_user" not in texto
    assert "password" not in texto.lower().split('"""')[2] if '"""' in texto else True


# =============================================================================
# El rol se lee de la base, no del token
# =============================================================================


def test_el_rol_se_consulta_en_cada_peticion():
    """Un token viejo no puede conservar el poder después de que se lo quiten.

    Todos los demás roles viajan en el JWT y está bien: quedan dentro de la
    empresa donde ya estaban. Este cruza organizaciones, así que revocarlo tiene
    que surtir efecto en el momento - que es cuando más importa.
    """
    texto = Path("api/deps.py").read_text(encoding="utf-8")
    cuerpo = texto.split("def require_platform_admin", 1)[1].split("\nPlatformAdminDep", 1)[0]

    assert "SELECT is_platform_admin FROM core.app_user" in cuerpo, (
        "require_platform_admin dejó de consultar la base. Si el flag pasó al "
        "JWT, revocar el rol deja de tener efecto inmediato."
    )
    # Y no se cuela como claim del token en ningún sitio.
    seguridad = Path("api/security.py").read_text(encoding="utf-8")
    assert "is_platform_admin" not in seguridad


# =============================================================================
# Las capturas dejaron de ser de una empresa
# =============================================================================


def test_las_capturas_ya_no_se_filtran_por_empresa():
    """El error que originó todo esto: la captura le llegaba al comerciante.

    Un contrato de login describe cómo funciona Effi y es el mismo para todos.
    Filtrarlo por el tenant de quien pregunta lo encerraba en la empresa del
    cliente que lo pidió, donde no lo ve quien tiene que cablear el conector.
    """
    texto = Path("api/routers/captures.py").read_text(encoding="utf-8")
    listado = texto.split("def list_captures", 1)[1].split("\n@router", 1)[0]

    assert "user.tenant_id" not in listado, (
        "list_captures volvió a filtrar por empresa. Vuelve a esconderle la "
        "captura a quien tiene que leerla."
    )
    assert "require_platform_admin" in texto


def test_el_trigger_de_secretos_sobrevivio_al_cambio():
    """Quitar la RLS por empresa no podía llevarse por delante la otra defensa.

    Son cosas distintas: la RLS aislaba datos que no existían, el trigger impide
    que entre una credencial. Verificado contra Postgres real: sigue en pie.
    """
    sql = _sql_053()
    assert "reject_capture_secrets" in sql
    assert "DROP TRIGGER" not in sql.split("reject_capture_secrets")[1][:400], (
        "La 053 borró el trigger que rechaza secretos."
    )


@pytest.mark.parametrize("ruta", ["/plataforma", "/connections"])
def test_las_paginas_a_las_que_apuntan_los_avisos_existen(ruta):
    """Un aviso que lleva a un 404 es peor que no avisar."""
    assert Path(f"web/app{ruta}").is_dir(), f"web/app{ruta} no existe"
