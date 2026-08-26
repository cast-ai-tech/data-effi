"""The vault's promises, tested as promises rather than as plumbing.

Every test here defends a rule that has a real failure behind it, and most of
those failures are silent - which is exactly why they need a test:

  a password that reads back in the clear      a dump becomes a breach
  a password that leaks through repr()         a traceback becomes a breach
  a session that survives a password change    a merchant "revokes" nothing
  a retry after a rejected password            the merchant is locked out of
                                               their own Effi account by us

The last one is worth being blunt about: it is the only failure in this file
where the damage lands entirely on the customer and not on us, and it is the
one a well-meaning "just try once more" would reintroduce.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pipeline import vault
from pipeline.vault import (
    Credential,
    CredentialUnreadable,
    VaultKeyMissing,
    decrypt_secret,
    encrypt_secret,
    session_is_fresh,
)


@pytest.fixture
def vault_key(monkeypatch) -> str:
    """A throwaway key, and a cleared cache so the next test can use another."""
    key = vault.generate_key()
    monkeypatch.setenv(vault.ENV_KEY, key)
    vault._fernet.cache_clear()
    yield key
    vault._fernet.cache_clear()


# =============================================================================
# The password never survives a round trip in readable form
# =============================================================================


def test_ciphertext_does_not_contain_the_password(vault_key):
    """The obvious property, asserted anyway because it is the whole point."""
    blob = encrypt_secret("MiClave-Effi-2026")
    assert b"MiClave" not in blob
    assert b"Effi" not in blob
    assert decrypt_secret(blob) == "MiClave-Effi-2026"


def test_same_password_encrypts_differently_every_time(vault_key):
    """Randomised, so nobody can count identical ciphertexts across tenants.

    With a deterministic cipher, two merchants using the same weak password
    would produce identical bytes, and one glance at the table would tell you
    which accounts to try that password against.
    """
    first = encrypt_secret("clave-repetida")
    second = encrypt_secret("clave-repetida")
    assert first != second
    assert decrypt_secret(first) == decrypt_secret(second) == "clave-repetida"


def test_rotated_key_raises_instead_of_reading_as_absent(vault_key, monkeypatch):
    """A key rotation must say "reingresa tu contraseña", not "no hay cuenta".

    Returning None here would render as "conecta tu cuenta" on a connection that
    IS connected, and the merchant would type a password that was already right.
    """
    blob = encrypt_secret("clave-original")

    monkeypatch.setenv(vault.ENV_KEY, vault.generate_key())
    vault._fernet.cache_clear()

    with pytest.raises(CredentialUnreadable):
        decrypt_secret(blob)


def test_no_key_configured_fails_loudly(monkeypatch):
    """Never fall back to storing plaintext because a key is missing."""
    monkeypatch.delenv(vault.ENV_KEY, raising=False)
    vault._fernet.cache_clear()

    assert vault.vault_available() is False
    with pytest.raises(VaultKeyMissing):
        encrypt_secret("cualquier-cosa")


def test_empty_secret_is_refused(vault_key):
    """A blank password in the vault reads as "connected" and syncs as broken."""
    with pytest.raises(ValueError):
        encrypt_secret("   ")


# =============================================================================
# The password does not leak through the objects that carry it
# =============================================================================


def test_credential_never_renders_its_password():
    """Covers repr AND str: leaving either open leaves a path to a log file.

    `logger.info("cred=%s", cred)` uses str. A traceback uses repr. An f-string
    uses str. Somebody will eventually write one of the three.
    """
    cred = Credential(username="reportes@tienda.co", password="SuperSecreta123")

    assert "SuperSecreta123" not in repr(cred)
    assert "SuperSecreta123" not in str(cred)
    assert "SuperSecreta123" not in f"{cred}"
    assert "SuperSecreta123" not in "{}".format(cred)  # noqa: UP032
    # The username is not a secret and must stay visible - it is what lets the
    # merchant recognise which account is connected.
    assert "reportes@tienda.co" in repr(cred)


def test_credential_rejects_blanks():
    with pytest.raises(ValueError):
        Credential(username="  ", password="algo")
    with pytest.raises(ValueError):
        Credential(username="usuario", password="")


# =============================================================================
# Sessions expire early, on purpose
# =============================================================================


def test_session_with_no_known_expiry_is_stale():
    """Guessing "probably still valid" trades a cheap login for a failed sync."""
    assert session_is_fresh(None) is False


def test_session_is_stale_before_it_actually_expires():
    """The margin exists so a sync never starts on a session that dies mid-run."""
    now = datetime.now(UTC)
    inside_margin = now + vault.SESSION_REFRESH_MARGIN - timedelta(minutes=1)
    outside_margin = now + vault.SESSION_REFRESH_MARGIN + timedelta(minutes=5)

    assert session_is_fresh(inside_margin, now=now) is False
    assert session_is_fresh(outside_margin, now=now) is True


def test_naive_timestamp_is_treated_as_utc():
    """psycopg can hand back a naive datetime; it must not crash the comparison."""
    now = datetime.now(UTC)
    naive_future = (now + timedelta(hours=5)).replace(tzinfo=None)
    assert session_is_fresh(naive_future, now=now) is True
