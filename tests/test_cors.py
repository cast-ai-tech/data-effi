"""Which browser origins the API lets in, now that the web lives on Vercel.

No database: a CORS preflight is answered by the middleware before any route
runs, so the app is built but its lifespan (the connection pool) never starts.

Why this exists: file uploads go from the browser straight to the API, and the
web moved from Netlify to Vercel on 2026-08-24. Vercel gives every branch and
pull request its own URL, so exact origins are not enough: the production URL
and the previews match a regex (`CORS_ORIGIN_REGEX`, shipped in render.yaml),
while the exact list (`CORS_ORIGINS`) keeps Netlify while it stays as backup.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("fastapi")

VERCEL_REGEX = r"^https://masterdataweb(-[a-z0-9-]+)?\.vercel\.app$"
NETLIFY = "https://data-effi.netlify.app"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    previous = {
        key: os.environ.get(key)
        for key in ("DATABASE_URL", "CORS_ORIGINS", "CORS_ORIGIN_REGEX")
    }
    os.environ.setdefault("DATABASE_URL", "postgresql://norte_app:x@localhost:5432/norte")
    os.environ.setdefault("JWT_SECRET", "t" * 48)
    os.environ.setdefault("PII_HASH_SALT", "s" * 48)
    os.environ.setdefault("WORKER_TRIGGER_SECRET", "w" * 48)
    os.environ["CORS_ORIGINS"] = f"http://localhost:3000,{NETLIFY}"
    os.environ["CORS_ORIGIN_REGEX"] = VERCEL_REGEX

    from api.settings import get_settings

    get_settings.cache_clear()

    from api.main import create_app

    # No `with`: the lifespan (and the database pool) must not start.
    yield TestClient(create_app())

    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    get_settings.cache_clear()


def _preflight(client, origin: str):
    return client.options(
        "/ingest/upload",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        },
    )


@pytest.mark.parametrize(
    "origin",
    [
        "https://masterdataweb.vercel.app",                       # production
        "https://masterdataweb-git-main-cast-ai-tech.vercel.app",  # branch preview
        "https://masterdataweb-abc123def-cast-ai-tech.vercel.app", # deployment preview
    ],
)
def test_vercel_production_and_previews_pass_preflight(client, origin):
    response = _preflight(client, origin)
    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == origin
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_the_netlify_backup_still_passes_while_it_is_listed(client):
    response = _preflight(client, NETLIFY)
    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == NETLIFY


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "https://masterdataweb.vercel.app.evil.example",   # suffix trick
        "https://notmasterdataweb.vercel.app",              # prefix trick
        "https://other-project-abc123.vercel.app",          # someone else's Vercel app
        "http://masterdataweb.vercel.app",                  # plain HTTP
    ],
)
def test_anything_else_is_refused(client, origin):
    response = _preflight(client, origin)
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
