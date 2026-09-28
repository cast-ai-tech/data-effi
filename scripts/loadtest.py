"""Prueba de carga del API - SOLO contra un entorno de desarrollo.

Sin dependencias nuevas: asyncio + httpx (ya están en requirements.txt).

PREPARACIÓN (una base desechable, nunca producción):

    # 1. Postgres desechable y el esquema como en CI
    docker run -d --rm --name dataeffi-stress -e POSTGRES_USER=norte \
        -e POSTGRES_DB=norte -e POSTGRES_PASSWORD=... -p 55440:5432 postgres:16-alpine
    POSTGRES_ADMIN_URL=... python -m scripts.migrate
    POSTGRES_ADMIN_URL=... POSTGRES_APP_PASSWORD=... python -m scripts.setup_roles
    # 2. ~200k guías en tres empresas, usuarios con roles distintos
    DATABASE_URL=postgresql://norte_app:...@host:55440/norte python -m scripts.seed_stress
    # 3. El API local, conectado como norte_app (RLS activo)
    uvicorn api.main:app --port 18765

ESCENARIOS:

    python -m scripts.loadtest reads   --users 20,50,100 --duration 30
    python -m scripts.loadtest upload  --users 20 --upload-mb 25
    python -m scripts.loadtest login   --burst 40
    python -m scripts.loadtest worker  --users 20      (necesita WORKER_TRIGGER_SECRET)
    python -m scripts.loadtest all     --json resultados.json

    reads   lecturas del tablero (kpis país/global/informe, órdenes con búsqueda
            y paginación, clientes, productos) con N usuarios concurrentes.
    upload  un archivo grande (csv/xlsx) mientras siguen las lecturas; mide que
            /health siga contestando.
    login   ráfaga de logins: comprueba que el limitador corta con 429 y no con
            500 ni cuelgues.
    worker  POST /worker/trigger/{job} mientras siguen las lecturas.

Se niega a correr contra *.onrender.com, *.supabase.* o *.vercel.app.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import random
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urlsplit

import httpx

PASSWORD = os.environ.get("STRESS_PASSWORD", "stress-masterdata-2026")
ACCOUNTS = [
    "owner@stress-a.masterdata.dev",
    "analyst@stress-a.masterdata.dev",
    "viewer@stress-a.masterdata.dev",
    "owner@stress-b.masterdata.dev",
    "owner@stress-c.masterdata.dev",
]
FORBIDDEN_HOSTS = ("onrender.com", "supabase.co", "supabase.com", "vercel.app")


@dataclass
class Stats:
    latencies: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    errors: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    codes: dict[str, dict[int, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int))
    )

    def record(self, name: str, seconds: float, code: int) -> None:
        self.latencies[name].append(seconds)
        self.codes[name][code] += 1
        if code >= 500 or code == 0:
            self.errors[name] += 1

    def rows(self) -> list[dict]:
        out = []
        for name in sorted(self.latencies):
            values = sorted(self.latencies[name])
            n = len(values)
            out.append({
                "endpoint": name,
                "n": n,
                "p50_ms": round(statistics.median(values) * 1000),
                "p95_ms": round(values[min(n - 1, int(n * 0.95))] * 1000),
                "max_ms": round(values[-1] * 1000),
                "err_pct": round(100 * self.errors[name] / n, 1),
                "codes": dict(self.codes[name]),
            })
        return out


def print_table(title: str, rows: list[dict]) -> None:
    print(f"\n== {title}")
    print(f"{'endpoint':<34}{'n':>6}{'p50':>8}{'p95':>8}{'max':>8}{'err%':>7}  códigos")
    for r in rows:
        print(
            f"{r['endpoint']:<34}{r['n']:>6}{r['p50_ms']:>8}{r['p95_ms']:>8}"
            f"{r['max_ms']:>8}{r['err_pct']:>7}  {r['codes']}"
        )


def read_requests(country: str) -> list[tuple[str, str, dict]]:
    """Lo que pide un tablero real al abrirse y al navegar. (nombre, ruta, params)."""
    today = date.today()
    last30 = {"country": country, "date_from": str(today - timedelta(days=30)),
              "date_to": str(today)}
    last90 = {**last30, "date_from": str(today - timedelta(days=90))}
    page = random.randint(1, 40)
    return [
        ("kpis/global", "/kpis/global", {}),
        ("kpis/layout", "/kpis/layout", {"country": country}),
        ("kpis/daily-contribution", "/kpis/daily-contribution", last30),
        ("kpis/contribution-split", "/kpis/contribution-split", last30),
        ("kpis/carriers", "/kpis/carriers", last30),
        ("kpis/geo", "/kpis/geo", last30),
        ("kpis/products", "/kpis/products", last90),
        ("kpis/cohorts", "/kpis/cohorts", {"country": country}),
        ("kpis/aging", "/kpis/aging", {"country": country}),
        ("kpis/cash-cycle", "/kpis/cash-cycle", last30),
        ("kpis/carrier-by-zone", "/kpis/carrier-by-zone", last30),
        ("informe/daily-status", "/kpis/daily-status", {"country": country}),
        ("informe/platforms", "/kpis/platforms", {"country": country}),
        ("orders/page", "/orders", {"country": country, "page": page}),
        ("orders/search", "/orders", {"country": country, "search": f"{country}-26"}),
        ("orders/open", "/orders", {"country": country, "only_open": "true"}),
        ("customers/page", "/customers", {"country": country, "page": page}),
        ("customers/range", "/customers", last30),
        ("products", "/products", {}),
        ("kpis/products-all", "/kpis/products", {"country": country}),
    ]


async def login(
    client: httpx.AsyncClient, email: str, ip: str | None = None
) -> tuple[int, str | None]:
    # Con PROXY_SHARED_SECRET el API cree la IP que manda el proxy de la web:
    # así se simulan personas distintas, cada una con su propio cupo.
    headers = {}
    secret = os.environ.get("PROXY_SHARED_SECRET")
    if ip and secret:
        headers = {"X-Proxy-Secret": secret, "X-Client-IP": ip}
    r = await client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}, headers=headers
    )
    if r.status_code != 200:
        return r.status_code, None
    return 200, r.json()["access_token"]


async def get_tokens(client: httpx.AsyncClient) -> list[tuple[str, str]]:
    tokens = []
    for email in ACCOUNTS:
        code, token = await login(client, email)
        if token is None:
            raise SystemExit(f"login de {email} falló con {code}")
        tokens.append((email, token))
    return tokens


async def reader(client, token, country, stats: Stats, stop_at: float) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    while time.monotonic() < stop_at:
        for name, path, params in random.sample(read_requests(country), 6):
            if time.monotonic() >= stop_at:
                return
            started = time.monotonic()
            try:
                r = await client.get(path, params=params, headers=headers)
                code = r.status_code
            except httpx.HTTPError:
                code = 0
            stats.record(name, time.monotonic() - started, code)
        # Una persona mira la pantalla un rato antes de pedir la siguiente.
        await asyncio.sleep(random.uniform(0.2, 1.0))


async def health_probe(client, stats: Stats, stop_at: float) -> None:
    while time.monotonic() < stop_at:
        started = time.monotonic()
        try:
            code = (await client.get("/health")).status_code
        except httpx.HTTPError:
            code = 0
        stats.record("health", time.monotonic() - started, code)
        await asyncio.sleep(0.5)


def country_for(email: str) -> str:
    # El viewer de stress-a solo puede leer CO.
    return "CO" if email.startswith("viewer") else random.choice(["CO", "EC", "GT"])


async def run_reads(client, tokens, users: int, duration: float, extra=None) -> Stats:
    stats = Stats()
    stop_at = time.monotonic() + duration
    tasks = []
    for i in range(users):
        email, token = tokens[i % len(tokens)]
        tasks.append(reader(client, token, country_for(email), stats, stop_at))
    tasks.append(health_probe(client, stats, stop_at))
    if extra is not None:
        tasks.append(extra(stats))
    await asyncio.gather(*tasks)
    return stats


def build_upload(mb: float, kind: str) -> tuple[str, bytes, str]:
    """Un reporte de guías con la forma del de Effi (las columnas que se leen), del
    tamaño pedido. Se sube como plataforma `effi` a Ecuador."""
    header = (
        "Guía transportadora,Prefijo ID guía,Fecha de creación,Estado global guía inicial,"
        "Nombre transportadora Efficommerce,Departamento destinatario,Ciudad destinatario,"
        "País destinatario,Destinatario,Teléfonos destinatario,Contenido,Valor recaudo,"
        "Precio flete total a cliente\n"
    )
    states = ["Entregada", "En tránsito", "Devolución", "Novedad"]
    rows = []
    size = len(header)
    i = 0
    today = date.today()
    while size < mb * 1024 * 1024:
        i += 1
        line = (
            f"LT{i:09d},EF{i:08d},{today - timedelta(days=i % 200)},{states[i % 4]},"
            f"Servientrega,Pichincha,Quito,Ecuador,Cliente {i % 9973},09{i:08d},"
            f"Producto {i % 37} x 1,{25 + i % 50}.90,2.95\n"
        )
        rows.append(line)
        size += len(line)
    if kind == "csv":
        return "carga_estres.csv", (header + "".join(rows)).encode(), "text/csv"
    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    ws = wb.create_sheet()
    ws.append(header.strip().split(","))
    for line in rows:
        ws.append(line.strip().split(","))
    buf = io.BytesIO()
    wb.save(buf)
    return (
        "carga_estres.xlsx", buf.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


async def main_async(args) -> dict:
    host = urlsplit(args.base_url).hostname or ""
    if any(host.endswith(h) for h in FORBIDDEN_HOSTS):
        raise SystemExit(f"Me niego a cargar {host}: esto es para desarrollo.")

    limits = httpx.Limits(max_connections=500, max_keepalive_connections=500)
    timeout = httpx.Timeout(args.timeout)
    results: dict = {}
    async with httpx.AsyncClient(base_url=args.base_url, limits=limits, timeout=timeout) as client:
        scenarios = (
            ["reads", "upload", "login", "worker"] if args.scenario == "all" else [args.scenario]
        )
        tokens = await get_tokens(client) if scenarios != ["login"] else []

        if "reads" in scenarios:
            for users in args.users:
                stats = await run_reads(client, tokens, users, args.duration)
                rows = stats.rows()
                print_table(f"lecturas, {users} usuarios, {args.duration}s", rows)
                results[f"reads_{users}"] = rows

        if "upload" in scenarios:
            filename, payload, ctype = build_upload(args.upload_mb, args.upload_kind)
            print(f"\narchivo de {len(payload) / 1e6:.1f} MB ({filename})")
            owner = tokens[0][1]

            async def uploader(stats: Stats) -> None:
                await asyncio.sleep(2)
                started = time.monotonic()
                try:
                    r = await client.post(
                        "/ingest/upload",
                        headers={"Authorization": f"Bearer {owner}"},
                        data={"platform_code": "effi", "country_code": "EC",
                              "kind": "shipments", "reprocess": "true"},
                        files={"files": (filename, payload, ctype)},
                        timeout=300,
                    )
                    code = r.status_code
                    if code >= 400:
                        print("upload:", code, r.text[:300])
                except httpx.HTTPError as exc:
                    code = 0
                    print("upload:", type(exc).__name__)
                stats.record("upload", time.monotonic() - started, code)

            users = args.users[0]
            stats = await run_reads(client, tokens, users, args.duration, extra=uploader)
            rows = stats.rows()
            print_table(f"subida de {args.upload_mb} MB + {users} lectores", rows)
            results["upload"] = rows

        if "login" in scenarios:
            # Dos ráfagas: una desde UNA sola IP (el limitador debe cortar con
            # 429) y otra desde IPs distintas (gente real entrando a la vez:
            # nadie debe ver 500 ni esperar sin respuesta).
            for label, same_ip in (("login_misma_ip", True), ("login_ips_distintas", False)):
                stats = Stats()

                async def one(
                    i: int, same_ip: bool = same_ip, stats: Stats = stats, label: str = label
                ) -> None:
                    started = time.monotonic()
                    email = ACCOUNTS[i % len(ACCOUNTS)]
                    ip = "203.0.113.7" if same_ip else f"198.51.{i // 250}.{i % 250 + 1}"
                    try:
                        code, _ = await login(client, email, ip)
                    except httpx.HTTPError:
                        code = 0
                    stats.record(label, time.monotonic() - started, code)

                await asyncio.gather(*(one(i) for i in range(args.burst)))
                rows = stats.rows()
                print_table(f"ráfaga de {args.burst} logins ({label})", rows)
                results[label] = rows

        if "worker" in scenarios:
            secret = os.environ.get("WORKER_TRIGGER_SECRET", "")
            jobs = ["relink_orphans", "calibrate_maturation", "daily_digest"]

            async def triggers(stats: Stats) -> None:
                async def fire(job: str) -> None:
                    started = time.monotonic()
                    try:
                        r = await client.post(
                            f"/worker/trigger/{job}", headers={"X-Worker-Secret": secret},
                            timeout=300,
                        )
                        code = r.status_code
                        if code >= 400:
                            print(job, code, r.text[:200])
                    except httpx.HTTPError:
                        code = 0
                    stats.record(f"worker/{job}", time.monotonic() - started, code)

                await asyncio.sleep(1)
                await asyncio.gather(*(fire(j) for j in jobs))

            users = args.users[0]
            stats = await run_reads(client, tokens, users, args.duration, extra=triggers)
            rows = stats.rows()
            print_table(f"jobs del worker + {users} lectores", rows)
            results["worker"] = rows
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Prueba de carga del API (solo desarrollo)")
    parser.add_argument("scenario", choices=["reads", "upload", "login", "worker", "all"])
    parser.add_argument("--base-url", default=os.environ.get("LOADTEST_URL", "http://127.0.0.1:18765"))
    parser.add_argument("--users", default="20,50,100",
                        type=lambda s: [int(x) for x in s.split(",")])
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--upload-mb", type=float, default=25)
    parser.add_argument("--upload-kind", choices=["csv", "xlsx"], default="xlsx")
    parser.add_argument("--burst", type=int, default=40)
    parser.add_argument("--json", help="Guardar los resultados en este archivo")
    args = parser.parse_args()
    results = asyncio.run(main_async(args))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
