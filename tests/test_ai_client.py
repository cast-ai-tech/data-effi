"""El presupuesto diario de IA cuenta lo que se factura (rama audit/pipeline-worker)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai import client as ai_client

genai = pytest.importorskip("google.genai")


class _FakeModels:
    def generate_content(self, **_kwargs):
        return SimpleNamespace(
            text="Resumen.",
            candidates=[],
            usage_metadata=SimpleNamespace(
                prompt_token_count=100,
                candidates_token_count=50,
                thoughts_token_count=450,
            ),
        )


class _FakeClient:
    def __init__(self, **_kwargs):
        self.models = _FakeModels()


def test_thinking_tokens_count_against_the_daily_budget(monkeypatch):
    monkeypatch.setattr(genai, "Client", _FakeClient)
    settings = SimpleNamespace(
        require_ai=lambda: None,
        gemini_api_key="k",
        ai_request_timeout_seconds=10,
        ai_model="gemini-test",
        ai_thinking_budget=128,
    )

    response = ai_client.call_llm(settings, system="s", user_message="u")

    assert response.input_tokens == 100
    assert response.output_tokens == 500   # 50 de respuesta + 450 pensando
