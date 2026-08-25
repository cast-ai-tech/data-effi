"""Tasas de cambio oficiales, banco central por banco central.

WHY THIS FILE EXISTS
A general FX provider quotes the market. An accounting department quotes the
regulator, and the two are not the same number: the free provider this project
shipped with was saying 3.663 pesos to the dollar the day Colombia's TRM was
3.048 - a 20% gap that would have distorted every consolidated total, silently.

So each currency gets its official source where one is reachable, and the
general provider is what fills the gaps.

WHAT "OFFICIAL" COSTS
There is no common interface. Every central bank publishes differently - REST,
SOAP, a CSV, a token you request by email - so each one is a small function
here rather than a row in a config table. That is the honest shape of the
problem, and pretending otherwise would mean a config table whose values are
"call this specific Python function".

STATUS PER CURRENCY

    COP   Superintendencia Financiera (TRM)      REST, sin credenciales   OK
    PEN   BCRP                                   REST, sin credenciales   OK
    CLP   Banco Central de Chile (observado)     REST, sin credenciales   OK
    GTQ   Banguat                                SOAP, sin credenciales   OK
    MXN   Banxico (FIX)                          REST, TOKEN requerido    pendiente
    CRC   BCCR                                   REST, TOKEN requerido    pendiente
    DOP   Banco Central RD                       sin API pública hallada  proveedor
    HNL   Banco Central de Honduras              sin API pública hallada  proveedor
    VES   BCV                                    solo web, sin API        proveedor
    USD   -                                      es la base               n/a

The two marked "TOKEN requerido" are free but need someone to register and paste
the token into the environment; they are wired up and skip themselves until then.
Everything else falls back to the general provider, which is fine for a country
Distrilatam does not operate in yet - and is stated on screen either way, because
a rate whose origin is unknown is a rate nobody should trust.

Every fetcher returns (units per USD, the date the source stamped it) or
(None, None). Failure is never fatal: the caller falls back.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Colombia - Tasa Representativa del Mercado, published by the regulator on the
# open-data portal. No key, no rate limit worth worrying about.
TRM_URL = "https://www.datos.gov.co/resource/32sa-8pi3.json"

# Perú - BCRP, serie PD04640PD: "TC Sistema bancario SBS (S/ por US$) - Venta".
BCRP_URL = "https://estadisticas.bcrp.gob.pe/estadisticas/series/api/PD04640PD/json"

# Chile - "dólar observado" del Banco Central, servido por mindicador.cl, que es
# un espejo estable de la serie oficial y no pide credenciales como la API del
# banco. El valor es el del banco; el intermediario solo lo republica.
MINDICADOR_URL = "https://mindicador.cl/api/dolar"

# Guatemala - Banguat. SOAP de los de verdad, con SOAPAction y sobre.
BANGUAT_URL = "https://www.banguat.gob.gt/variables/ws/TipoCambio.asmx"
BANGUAT_ENVELOPE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
    '<soap:Body><TipoCambioDia xmlns="http://www.banguat.gob.gt/variables/ws/" /></soap:Body>'
    "</soap:Envelope>"
)

# México - Banxico SIE, serie SF43718 (FIX). El token es gratuito y se pide en
# banxico.org.mx/SieAPIRest; sin él esta fuente se salta sola.
BANXICO_URL = (
    "https://www.banxico.org.mx/SieAPIRest/service/v1/series/SF43718/datos/oportuno"
)

# Costa Rica - BCCR, indicador 318 (venta). El token se pide por correo y viene
# acompañado del correo con el que se registró: ambos van en cada petición.
BCCR_URL = "https://gee.bccr.fi.cr/Indicadores/Suscripciones/WS/wsindicadoreseconomicos.asmx/ObtenerIndicadoresEconomicos"


def _trm_colombia(client: Any) -> tuple[float | None, date | None]:
    response = client.get(TRM_URL, params={"$order": "vigenciadesde DESC", "$limit": 1})
    response.raise_for_status()
    rows = response.json()
    if not rows:
        return None, None
    return float(rows[0]["valor"]), datetime.fromisoformat(rows[0]["vigenciadesde"]).date()


def _bcrp_peru(client: Any) -> tuple[float | None, date | None]:
    """El BCRP devuelve la serie completa; interesa el último período con valor.

    Los fines de semana y feriados vienen sin dato ("n.d."), así que se recorre
    hacia atrás en vez de tomar el último a ciegas.
    """
    response = client.get(f"{BCRP_URL}/{date.today().replace(day=1)}/{date.today()}")
    response.raise_for_status()
    periods = response.json().get("periods") or []

    for period in reversed(periods):
        values = period.get("values") or []
        if not values:
            continue
        try:
            rate = float(values[0])
        except (TypeError, ValueError):
            continue  # "n.d." en feriados
        if rate > 0:
            return rate, _parse_bcrp_date(period.get("name"))
    return None, None


_BCRP_MONTHS = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}


def _parse_bcrp_date(name: str | None) -> date | None:
    """'22.Ago.26' -> date(2026, 8, 22). Devuelve None si cambia el formato."""
    if not name:
        return None
    try:
        day, month, year = name.split(".")
        return date(2000 + int(year), _BCRP_MONTHS[month.lower()[:3]], int(day))
    except (ValueError, KeyError):
        return None


def _bcch_chile(client: Any) -> tuple[float | None, date | None]:
    response = client.get(MINDICADOR_URL)
    response.raise_for_status()
    serie = response.json().get("serie") or []
    if not serie:
        return None, None
    latest = serie[0]
    return float(latest["valor"]), datetime.fromisoformat(
        latest["fecha"].replace("Z", "+00:00")
    ).date()


def _banguat_guatemala(client: Any) -> tuple[float | None, date | None]:
    """Banguat habla SOAP, y su respuesta se lee con una expresión regular.

    Un parser de XML completo para dos etiquetas sería más ceremonia que
    beneficio, y el formato lleva años sin cambiar. Si cambia, esto devuelve
    None y el proveedor general toma el relevo - que es exactamente lo que debe
    pasar cuando una fuente deja de ser legible.
    """
    response = client.post(
        BANGUAT_URL,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "http://www.banguat.gob.gt/variables/ws/TipoCambioDia",
        },
        content=BANGUAT_ENVELOPE,
    )
    response.raise_for_status()
    body = response.text

    referencia = re.search(r"<referencia>([\d.]+)</referencia>", body)
    fecha = re.search(r"<fecha>(\d{2}/\d{2}/\d{4})</fecha>", body)
    if not referencia:
        return None, None

    stamped = None
    if fecha:
        try:
            stamped = datetime.strptime(fecha.group(1), "%d/%m/%Y").date()
        except ValueError:
            stamped = None
    return float(referencia.group(1)), stamped


def _banxico_mexico(client: Any) -> tuple[float | None, date | None]:
    token = os.environ.get("BANXICO_TOKEN", "").strip()
    if not token:
        return None, None  # sin token, esta fuente no existe

    response = client.get(BANXICO_URL, headers={"Bmx-Token": token})
    response.raise_for_status()
    series = response.json()["bmx"]["series"][0]["datos"]
    if not series:
        return None, None
    dato = series[-1]
    return float(dato["dato"]), datetime.strptime(dato["fecha"], "%d/%m/%Y").date()


def _bccr_costa_rica(client: Any) -> tuple[float | None, date | None]:
    token = os.environ.get("BCCR_TOKEN", "").strip()
    correo = os.environ.get("BCCR_EMAIL", "").strip()
    if not token or not correo:
        return None, None

    hoy = date.today().strftime("%d/%m/%Y")
    response = client.get(
        BCCR_URL,
        params={
            "Indicador": "318",  # tipo de cambio de venta
            "FechaInicio": hoy,
            "FechaFinal": hoy,
            "Nombre": "MasterData",
            "SubNiveles": "N",
            "CorreoElectronico": correo,
            "Token": token,
        },
    )
    response.raise_for_status()
    valor = re.search(r"<NUM_VALOR>([\d.]+)</NUM_VALOR>", response.text)
    if not valor:
        return None, None
    return float(valor.group(1)), date.today()


# La moneda -> cómo se consigue su tasa oficial, y con qué nombre se marca en
# core.fx_rate.source para que la pantalla pueda decir de dónde salió.
OFFICIAL_SOURCES: dict[str, tuple[str, Callable[[Any], tuple[float | None, date | None]]]] = {
    "COP": ("trm_oficial", _trm_colombia),
    "PEN": ("oficial_bcrp", _bcrp_peru),
    "CLP": ("oficial_bcch", _bcch_chile),
    "GTQ": ("oficial_banguat", _banguat_guatemala),
    "MXN": ("oficial_banxico", _banxico_mexico),
    "CRC": ("oficial_bccr", _bccr_costa_rica),
}


def fetch_official_rates(client: Any, currencies: tuple[str, ...]) -> dict[str, tuple[float, str]]:
    """Todas las oficiales que se puedan conseguir, sin dejar caer el trabajo.

    Devuelve {moneda: (unidades por dólar, nombre de la fuente)}. Una fuente que
    falla se registra y se omite: el proveedor general cubre esa moneda, y una
    caída del Banguat no puede dejar sin actualizar al resto del continente.
    """
    obtenidas: dict[str, tuple[float, str]] = {}

    for currency in currencies:
        entry = OFFICIAL_SOURCES.get(currency)
        if entry is None:
            continue
        nombre, fetcher = entry
        try:
            rate, _stamped = fetcher(client)
        except Exception:
            logger.warning("fuente oficial de %s no disponible (%s)", currency, nombre,
                           exc_info=True)
            continue
        if rate and rate > 0:
            obtenidas[currency] = (rate, nombre)

    return obtenidas
