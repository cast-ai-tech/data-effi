"""The preflight: turning "HTTP 403" into "te falta un permiso en Effi".

WHY THIS IS WORTH A MODULE
--------------------------
Effi's roles are granular. A merchant who creates a dedicated user for Master Data
will, on the first try, forget one of them - that is not a failure of the
merchant, it is what a list of eleven checkboxes does to anybody. What happens
next decides whether the product feels workable:

  WITHOUT a preflight   the sync runs at 3am, one export 403s, the connection
                        goes to `error`, and the merchant reads "HTTP 403" the
                        next morning. That is a support ticket, and it costs us
                        more than the customer is worth.

  WITH a preflight      the merchant clicks "Probar conexión", and the screen
                        says: "Guías de transporte ✓, Novedades de guías ✗ —
                        falta este permiso en Effi, sin él no sabrás por qué
                        fallan tus entregas." They fix it in two minutes,
                        themselves, at a moment when they are already looking
                        at the screen.

The whole value is in the second column of `PERMISSION_PROBES`: what each
permission is FOR, in the merchant's language. A checklist that only says
"denied" is a worse version of the 403.

WHAT A PROBE IS ALLOWED TO DO
-----------------------------
The narrowest possible read: one export, one single day, and the result is
thrown away. It never ingests, never writes, never widens the range to "see if
there is data". A probe answers exactly one question - may we read this? - and
a probe that downloads a year of guides to answer it would be abusing the
merchant's account to check whether we may use the merchant's account.

Required permissions are probed first and, if a required one is denied, the rest
are skipped: the connection cannot work anyway, and there is no reason to make
nine more requests to a panel that has already said no.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PermissionProbe:
    """How to test one permission, and what it costs the merchant to lack it."""

    code: str
    name: str
    # The export path this permission gates. Empty means "we know the permission
    # exists but have no endpoint to test it with" - reported as `unknown`, never
    # guessed as granted.
    path: str
    required: bool


# Ordered required-first, which is also the order the screen renders. Paths mirror
# connectors/effi/session_fetcher.py::REPORT_PATHS.
#
# Solo DOS están confirmadas contra un Effi real (captura del 2026-08-26): guías y
# movimientos, y no viven en /reportes/*/export como se supuso, sino en
# /app/<vista>/excel. Las otras cuatro siguen con la ruta supuesta y SON
# SOSPECHOSAS: la captura probó que ese patrón /reportes/ no existe en el Effi
# real, así que casi seguro también van bajo /app/. Se corregirán cuando una
# captura las traiga; overridables por entorno mientras tanto.
PERMISSION_PROBES: tuple[PermissionProbe, ...] = (
    PermissionProbe("guias_transporte", "Guías de transporte",
                    "/app/guia_transporte/excel", required=True),
    PermissionProbe("novedades_guias", "Novedades de guías de transporte",
                    "/reportes/novedades/export", required=True),
    PermissionProbe("trazabilidad_dinero", "Trazabilidad de dinero Effi",
                    "/app/movimiento_dinero_effi/excel", required=True),
    PermissionProbe("gestion_novedades", "Gestión de novedades de guías de transporte",
                    "/reportes/gestion-novedades/export", required=False),
    PermissionProbe("articulos", "Artículos", "/reportes/articulos/export", required=False),
    PermissionProbe("clientes", "Clientes", "/reportes/clientes/export", required=False),
    # Known to exist, no export endpoint confirmed. Reported honestly as unknown
    # rather than assumed either way.
    PermissionProbe("notas_remision", "Notas de remisión de venta", "", required=False),
    PermissionProbe("seguimientos_comerciales", "Seguimientos comerciales", "", required=False),
    PermissionProbe("mensajes_chat", "Mensajes de Chat", "", required=False),
    PermissionProbe("mensajes_sms", "Mensajes de texto SMS", "", required=False),
    PermissionProbe("consola_atencion", "Consola de atención", "", required=False),
)


@dataclass(slots=True)
class ProbeResult:
    """One permission, checked."""

    code: str
    name: str
    status: str          # granted | denied | unreachable | unknown
    detail: str
    required: bool
    checked_at: datetime


@dataclass(slots=True)
class PreflightReport:
    """Everything the "Probar conexión" button needs to render."""

    results: list[ProbeResult]
    session_valid: bool

    @property
    def missing_required(self) -> list[ProbeResult]:
        return [r for r in self.results if r.required and r.status == "denied"]

    @property
    def missing_optional(self) -> list[ProbeResult]:
        return [r for r in self.results if not r.required and r.status == "denied"]

    @property
    def unproven_required(self) -> list[ProbeResult]:
        """Required permissions we did not manage to confirm.

        Distinct from `missing_required`: nobody said no, we simply never got an
        answer. It still cannot be reported as working - "no pudimos comprobarlo"
        and "funciona" are not the same sentence, and only one of them is true.
        """
        return [
            r for r in self.results
            if r.required and r.status in ("unreachable", "unknown")
        ]

    @property
    def is_usable(self) -> bool:
        """True when the connection can actually produce a dashboard.

        Requires every required permission to have been AFFIRMATIVELY granted.
        Anything else - denied, unreachable, never probed - is not usable, and
        the distinction between "no" and "no answer" lives in the summary, where
        it tells the merchant whether to fix something or just try again.

        Optional permissions being denied does not make a connection unusable:
        that makes one card say "falta un dato". Refusing the whole connection
        over it would cost the merchant every metric to protect one card.
        """
        return (
            self.session_valid
            and not self.missing_required
            and not self.unproven_required
        )

    def credential_status(self) -> str:
        """The single word for `core.connection.credential_status`.

        An unproven required permission stays `insufficient_permissions` rather
        than getting a status of its own. The merchant's next action is the same
        either way - press the button again and see - and a sixth status word
        would buy a distinction nobody acts on.
        """
        if not self.session_valid:
            return "expired"
        if self.missing_required or self.unproven_required:
            return "insufficient_permissions"
        return "ok"

    def summary(self) -> str:
        """One line for the merchant. Says what to do, not what happened."""
        if not self.session_valid:
            return ("La sesión de Effi no sirve. Vuelve a ingresar tu usuario y "
                    "contraseña.")
        if self.missing_required:
            names = ", ".join(r.name for r in self.missing_required)
            return (f"Falta este permiso en Effi: {names}. Agrégalo al usuario que "
                    "conectaste y prueba de nuevo — sin él el tablero no se puede "
                    "calcular.")
        if self.unproven_required:
            # Deliberately NOT phrased as a problem with their permissions. It
            # probably is not: Effi did not answer. Sending them to check a
            # permission that was fine wastes their time and their trust.
            names = ", ".join(r.name for r in self.unproven_required)
            return (f"No se pudo comprobar: {names}. Effi no respondió a tiempo; "
                    "vuelve a probar en un momento. No parece un problema de tus "
                    "permisos.")
        if self.missing_optional:
            names = ", ".join(r.name for r in self.missing_optional)
            return (f"La conexión funciona. Falta: {names} — el tablero principal "
                    "se calcula igual, pero esas secciones quedarán vacías.")
        return "La conexión funciona y tiene todos los permisos."


def run_preflight(
    fetcher,
    *,
    base_url: str,
    probe_date: date | None = None,
) -> PreflightReport:
    """Probe every permission with the narrowest read each one allows.

    `fetcher` is anything exposing `probe(path, params) -> str`, which returns
    one of the status words. `EffiSessionFetcher.probe` implements it; tests pass
    a fake. Keeping the transport out of here means this module can be tested
    without a network and without a credential.
    """
    from connectors.effi.session_fetcher import SessionExpiredError

    day = probe_date or (datetime.now(UTC).date() - timedelta(days=1))
    params = {"fecha_inicio": day.isoformat(), "fecha_fin": day.isoformat()}

    results: list[ProbeResult] = []
    session_valid = True
    skip_rest = False

    for probe in PERMISSION_PROBES:
        now = datetime.now(UTC)

        if not probe.path:
            results.append(ProbeResult(
                probe.code, probe.name, "unknown",
                "Effi no expone un reporte para este permiso, así que no se puede "
                "comprobar desde aquí.",
                probe.required, now,
            ))
            continue

        if skip_rest:
            results.append(ProbeResult(
                probe.code, probe.name, "unknown",
                "No se comprobó: falta un permiso obligatorio y la conexión no "
                "puede funcionar todavía.",
                probe.required, now,
            ))
            continue

        try:
            status = fetcher.probe(f"{base_url.rstrip('/')}{probe.path}", params)
        except SessionExpiredError:
            # The session died mid-preflight. Everything after this would report
            # `denied` for the wrong reason, which would send the merchant to fix
            # permissions that were never the problem.
            session_valid = False
            results.append(ProbeResult(
                probe.code, probe.name, "unreachable",
                "La sesión de Effi expiró durante la comprobación.",
                probe.required, now,
            ))
            skip_rest = True
            continue
        except Exception as exc:
            logger.warning("preflight %s: %s", probe.code, type(exc).__name__)
            results.append(ProbeResult(
                probe.code, probe.name, "unreachable",
                "No se pudo comprobar este permiso en este momento.",
                probe.required, now,
            ))
            continue

        detail = _detail_for(status, probe)
        results.append(ProbeResult(probe.code, probe.name, status, detail, probe.required, now))

        if status == "denied" and probe.required:
            skip_rest = True

    return PreflightReport(results=results, session_valid=session_valid)


def _detail_for(status: str, probe: PermissionProbe) -> str:
    if status == "granted":
        return "Effi permitió leer este reporte."
    if status == "denied":
        return (f"Effi negó el acceso. Agrega el permiso «{probe.name}» "
                "(Consultar" + (", Ver reportes" if probe.required else "") +
                ") al usuario que conectaste.")
    if status == "unreachable":
        return "No se pudo comprobar este permiso en este momento."
    return "Sin comprobar."
