"""Un archivo rechazado no deja a sus compañeros huérfanos en el disco.

Sin base de datos. Lo que se fija: si la carga falla a mitad, la transacción se
deshace (los jobs no existen) y los bytes que ya se habían escrito se borran,
porque el disco es persistente y ninguna cola volvería a mirarlos.
"""

from __future__ import annotations

import io
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")

from starlette.datastructures import UploadFile  # noqa: E402

import api.routers.ingest as ingest  # noqa: E402
from api.deps import CurrentUser  # noqa: E402
from api.errors import ApiError  # noqa: E402
from pipeline.models import BatchKind  # noqa: E402

TENANT = uuid4()


def _settings(tmp_path):
    return SimpleNamespace(
        upload_dir=str(tmp_path), max_upload_mb=1, max_upload_bytes=1024 * 1024
    )


def _file(name: str) -> UploadFile:
    return UploadFile(io.BytesIO(b"guia,estado\nG-1,Entregado\n"), filename=name)


@pytest.mark.asyncio
async def test_si_el_segundo_archivo_se_rechaza_el_primero_no_queda_en_disco(
    tmp_path, monkeypatch
):
    user = CurrentUser(id=uuid4(), tenant_id=TENANT, email="x@example.com", role="owner")
    monkeypatch.setattr(
        ingest, "fetch_one",
        lambda *_a, **_k: {"id": uuid4(), "country_code": "CO", "platform_code": "effi"},
    )
    now = "2026-09-01T00:00:00+00:00"
    monkeypatch.setattr(
        ingest, "fetch_required",
        lambda *_a, **_k: {
            "id": uuid4(), "filename": "a.csv", "kind": "shipments", "size_bytes": 1,
            "status": "queued", "batch_id": None, "error": None, "queued_at": now,
            "finished_at": None,
        },
    )
    monkeypatch.setattr(ingest, "emit", lambda *_a, **_k: None)

    def mismatch(filename, *_a):
        if filename == "b.csv":
            raise ApiError("platform_mismatch", "no es de esta plataforma", status_code=422)

    monkeypatch.setattr(ingest, "_refuse_platform_mismatch", mismatch)

    with pytest.raises(ApiError):
        await ingest.upload(
            conn=SimpleNamespace(commit=lambda: None),
            user=user,
            settings=_settings(tmp_path),
            files=[_file("a.csv"), _file("b.csv")],
            connection_id=uuid4(),
            kind=BatchKind.SHIPMENTS.value,
        )

    assert list(tmp_path.rglob("*.csv")) == []


def test_si_la_base_rechaza_el_job_del_webhook_el_archivo_se_borra(tmp_path, monkeypatch):
    @contextmanager
    def caida(*_a, **_k):
        raise RuntimeError("base caída")
        yield  # pragma: no cover

    monkeypatch.setattr(ingest, "connection", caida)
    target = {"id": uuid4(), "tenant_id": TENANT, "country_code": "CO"}

    with pytest.raises(RuntimeError):
        ingest._queue_webhook_job(
            target, "w.csv", b"guia\nG-1\n", BatchKind.SHIPMENTS, _settings(tmp_path)
        )

    assert list(tmp_path.rglob("*.csv")) == []
