"""Qué plan abre o cierra una empresa: el de su organización, no el de quien entra.

Sin base de datos: se sustituye la consulta del tenant y el estado del plan.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")

import api.billing as billing  # noqa: E402
import api.deps as deps  # noqa: E402
from api.errors import PaymentRequired  # noqa: E402

ORG_DE_LA_EMPRESA = uuid4()
ORG_DE_LA_PERSONA = uuid4()


def _state(blocked: bool):
    return SimpleNamespace(
        blocked=blocked, message="Tu prueba terminó", status="expired",
        trial_ends_at=SimpleNamespace(isoformat=lambda: "2026-08-01T00:00:00+00:00"),
    )


@pytest.fixture
def consulted(monkeypatch) -> list:
    seen: list = []
    vencidas = {ORG_DE_LA_EMPRESA}

    monkeypatch.setattr(
        deps, "fetch_one", lambda *_a, **_k: {"org_id": ORG_DE_LA_EMPRESA}
    )

    def subscription_state(_conn, org_id):
        seen.append(org_id)
        return _state(org_id in vencidas)

    monkeypatch.setattr(billing, "subscription_state", subscription_state)
    return seen


def test_un_invitado_de_otra_organizacion_no_abre_una_empresa_vencida(consulted):
    invitado = deps.CurrentUser(
        id=uuid4(), tenant_id=uuid4(), email="socio@example.com", role="viewer",
        org_id=ORG_DE_LA_PERSONA,
    )
    with pytest.raises(PaymentRequired):
        deps._guard_subscription(object(), invitado)
    assert consulted == [ORG_DE_LA_EMPRESA]


def test_sin_organizacion_propia_igual_se_mira_la_de_la_empresa(consulted):
    persona = deps.CurrentUser(
        id=uuid4(), tenant_id=uuid4(), email="x@example.com", role="owner", org_id=None
    )
    with pytest.raises(PaymentRequired):
        deps._guard_subscription(object(), persona)
    assert consulted == [ORG_DE_LA_EMPRESA]
