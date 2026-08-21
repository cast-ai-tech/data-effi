"""The Google Sheets connector, and the address it refuses to fetch.

This endpoint takes a URL from a user and then makes the SERVER open it. That is
the shape of every SSRF, so the guard is worth its own test: only https, only
docs.google.com, and no credentials smuggled into the authority part.

No network is touched here. Everything below is decided before a request would
be made.
"""

from __future__ import annotations

import pytest

from connectors.sheets.published_csv import (
    InvalidSheetUrlError,
    PublishedSheetFetcher,
    redact_url,
    validate_published_url,
)

GOOD = "https://docs.google.com/spreadsheets/d/e/2PACX-abc/pub?gid=0&single=true&output=csv"


def test_a_published_sheet_url_is_accepted():
    assert validate_published_url(GOOD) == GOOD
    assert validate_published_url(f"  {GOOD}  ") == GOOD


@pytest.mark.parametrize(
    "url",
    [
        "http://docs.google.com/spreadsheets/d/e/abc/pub?output=csv",   # not https
        "https://docs.google.com.evil.tld/pub?output=csv",              # suffix trick
        "https://evil.tld/docs.google.com/pub?output=csv",              # path trick
        "https://user:clave@docs.google.com/pub?output=csv",            # credentials
        "https://169.254.169.254/latest/meta-data/",                    # cloud metadata
        "http://localhost:8000/config/connections",                     # our own API
        "file:///etc/passwd",                                           # not even http
        "https://drive.google.com/uc?id=abc",                           # right vendor, wrong host
        "",
    ],
)
def test_anything_else_is_refused(url):
    with pytest.raises(InvalidSheetUrlError):
        validate_published_url(url)


def test_the_fetcher_refuses_to_be_built_with_a_bad_url():
    with pytest.raises(InvalidSheetUrlError):
        PublishedSheetFetcher(url="https://169.254.169.254/latest/meta-data/")


def test_the_query_string_never_reaches_a_log_line():
    redacted = redact_url(GOOD)
    assert redacted == "https://docs.google.com/spreadsheets/d/e/2PACX-abc/pub?***"
    assert "gid=" not in redacted
    assert "gid=" not in repr(PublishedSheetFetcher(url=GOOD))


def test_a_connection_without_a_url_or_an_env_var_says_so_in_spanish():
    from connectors.sheets.published_csv import SheetFetchError

    with pytest.raises(SheetFetchError) as excinfo:
        PublishedSheetFetcher.from_connection(source_url=None, secret_ref=None)
    assert "Publicar en la web" in str(excinfo.value)


def test_a_url_kept_in_an_env_var_still_works(monkeypatch):
    monkeypatch.setenv("SHEET_URL_TEST", GOOD)
    fetcher = PublishedSheetFetcher.from_connection(
        source_url=None, secret_ref="SHEET_URL_TEST"
    )
    assert redact_url(GOOD) in repr(fetcher)
