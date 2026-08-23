"""Which address the rate limiter keys on.

No database: `client_ip` only reads the request. The point being pinned down
is that a caller cannot pick their own bucket by writing `X-Forwarded-For`.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi import Request  # noqa: E402

from api.deps import client_ip, client_ip_inet  # noqa: E402


def _request(headers: dict[str, str] | None = None, peer: str | None = "10.0.0.9") -> Request:
    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw_headers,
        "client": (peer, 12345) if peer else None,
    }
    return Request(scope)


def test_without_a_proxy_the_socket_peer_is_the_caller():
    assert client_ip(_request()) == "10.0.0.9"


def test_the_last_forwarded_hop_wins_not_the_first():
    # The first entries are whatever the caller typed; the last one was appended
    # by the proxy in front of the API and is the only value it did not choose.
    request = _request({"X-Forwarded-For": "1.1.1.1, 2.2.2.2, 203.0.113.7"})
    assert client_ip(request) == "203.0.113.7"


def test_a_forged_first_entry_does_not_open_a_new_bucket():
    real = "203.0.113.7"
    first = client_ip(_request({"X-Forwarded-For": f"5.5.5.5, {real}"}))
    second = client_ip(_request({"X-Forwarded-For": f"6.6.6.6, {real}"}))
    assert first == second == real


def test_an_empty_forwarded_header_falls_back_to_the_peer():
    assert client_ip(_request({"X-Forwarded-For": " , "})) == "10.0.0.9"


def test_no_peer_at_all_is_still_a_string():
    assert client_ip(_request(peer=None)) == "unknown"


def test_only_a_real_ip_reaches_the_inet_column():
    assert client_ip_inet(_request({"X-Forwarded-For": "203.0.113.7"})) == "203.0.113.7"
    assert client_ip_inet(_request({"X-Forwarded-For": "not-an-ip"})) is None


def test_the_header_is_ignored_entirely_when_no_proxy_is_trusted(monkeypatch):
    from api import deps
    from api.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "trust_proxy_headers", False)
    try:
        assert deps.client_ip(_request({"X-Forwarded-For": "203.0.113.7"})) == "10.0.0.9"
    finally:
        monkeypatch.setattr(settings, "trust_proxy_headers", True)


def test_the_web_proxy_may_name_the_browser_only_with_the_shared_secret(monkeypatch):
    from api.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "proxy_shared_secret", "s" * 32)
    headers = {"X-Client-IP": "198.51.100.4", "X-Forwarded-For": "203.0.113.7"}
    assert client_ip(_request({**headers, "X-Proxy-Secret": "s" * 32})) == "198.51.100.4"
    # Wrong or missing secret: the claim is ignored, the chain is used.
    assert client_ip(_request({**headers, "X-Proxy-Secret": "x" * 32})) == "203.0.113.7"
    assert client_ip(_request(headers)) == "203.0.113.7"
