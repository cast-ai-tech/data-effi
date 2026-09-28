"""What the fetcher makes of each answer Effi can give to a report download.

The classification is the whole point: the worker decides from the exception
type whether to retry next run, ask the merchant to log in again, or ask them
to fix a permission. A wrong class sends the merchant to fix the wrong thing -
or parks a healthy connection forever.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from connectors.effi.session_fetcher import (
    EffiSessionFetcher,
    FetchError,
    PermissionDeniedError,
    SessionExpiredError,
    TransientFetchError,
)
from pipeline.models import BatchKind

HTML_TABLE_EXPORT = b"<html><body><table><tr><th>Guia</th></tr><tr><td>1</td></tr></table>"
LOGIN_FORM = b'<html><form action="/ingreso/validar_usuario"><input type="password" name="password"></form>'


@pytest.fixture
def effi(monkeypatch):
    seen: list[httpx.Request] = []
    answer: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        result = answer["response"]
        if isinstance(result, Exception):
            raise result
        return result

    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)
    return answer, seen


def _fetcher() -> EffiSessionFetcher:
    return EffiSessionFetcher(
        session_token="ci_session=abc",
        consent_granted_at=datetime.now(UTC),
        base_url="https://effi.test",
        min_interval_seconds=0,
    )


def _fetch():
    return _fetcher().fetch_report(
        BatchKind.SHIPMENTS, date_from=date(2026, 9, 1), date_to=date(2026, 9, 14)
    )


def test_an_html_table_export_is_a_report_not_an_expired_session(effi):
    """Effi's "Excel" is an HTML table. Rejecting it would fail every sync."""
    answer, _ = effi
    answer["response"] = httpx.Response(
        200, content=HTML_TABLE_EXPORT, headers={"content-type": "text/html; charset=utf-8"}
    )

    result = _fetch()

    assert result.payload == HTML_TABLE_EXPORT


def test_a_login_form_is_an_expired_session_whatever_the_content_type(effi):
    answer, _ = effi
    answer["response"] = httpx.Response(
        200, content=LOGIN_FORM, headers={"content-type": "application/vnd.ms-excel"}
    )

    with pytest.raises(SessionExpiredError):
        _fetch()


def test_a_403_is_a_missing_permission_not_an_expired_session(effi):
    """The preflight reads a 403 as `denied`; the sync must agree with it."""
    answer, _ = effi
    answer["response"] = httpx.Response(403)

    with pytest.raises(PermissionDeniedError) as exc:
        _fetch()

    assert not isinstance(exc.value, SessionExpiredError)
    assert "permiso" in str(exc.value)


@pytest.mark.parametrize(
    "response",
    [httpx.Response(429), httpx.Response(502), httpx.ConnectTimeout("lento")],
)
def test_outages_are_transient(effi, response):
    answer, _ = effi
    answer["response"] = response

    with pytest.raises(TransientFetchError):
        _fetch()


def test_a_404_is_not_transient(effi):
    """A moved endpoint will not fix itself on the next run."""
    answer, _ = effi
    answer["response"] = httpx.Response(404)

    with pytest.raises(FetchError) as exc:
        _fetch()

    assert not isinstance(exc.value, TransientFetchError)


def test_a_json_session_travels_as_a_bearer_header(effi, monkeypatch):
    """A token from a JSON login is not a cookie; sending it as one never worked."""
    monkeypatch.setenv("EFFI_SESSION_CARRIER", "json")
    answer, seen = effi
    answer["response"] = httpx.Response(
        200, content=b"a,b\n1,2\n", headers={"content-type": "text/csv"}
    )

    EffiSessionFetcher(
        session_token="tok-123",
        consent_granted_at=datetime.now(UTC),
        base_url="https://effi.test",
        min_interval_seconds=0,
    ).fetch_report(BatchKind.SHIPMENTS, date_from=date(2026, 9, 1), date_to=date(2026, 9, 2))

    assert seen[0].headers["authorization"] == "Bearer tok-123"
    assert "cookie" not in seen[0].headers


def test_the_probe_recognises_a_login_form_as_a_dead_session(effi):
    answer, _ = effi
    answer["response"] = httpx.Response(
        200, content=LOGIN_FORM, headers={"content-type": "text/html"}
    )

    with pytest.raises(SessionExpiredError):
        _fetcher().probe("https://effi.test/app/guia_transporte/excel", {})


def test_the_probe_grants_an_html_table_export(effi):
    answer, _ = effi
    answer["response"] = httpx.Response(
        200, content=HTML_TABLE_EXPORT, headers={"content-type": "text/html"}
    )

    assert _fetcher().probe("https://effi.test/app/guia_transporte/excel", {}) == "granted"
