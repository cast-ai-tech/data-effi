"""Reversible encryption for the few customer fields an operator must read.

WHY THIS EXISTS ALONGSIDE THE HASH
----------------------------------
`customer_hash` answers "is this the same person?" and nothing else. It is a
salted SHA-256, it is deterministic, and it is what every customer metric groups
by. It cannot be undone, which is the point - and also the reason it cannot fill
a column labelled "Teléfono" in the orders table.

So identity and display are stored separately, on purpose:

    customer_hash        deterministic, irreversible  -> grouping, metrics, joins
    customer_*_enc       randomised, reversible       -> the detail card, the table

Encrypting rather than storing in the clear buys three things:

1. A stolen dump, a leaked backup, or a `SELECT *` by a compromised read-only
   role yields ciphertext. The key is not in the database.
2. The key never appears in a SQL statement, so it cannot leak through
   `pg_stat_statements`, slow-query logs, or a query plan. Encryption and
   decryption happen in Python; Postgres only ever sees bytes.
3. Revoking access is possible. Rotating the key makes every stored value
   unreadable at once - something you cannot do once plaintext has been dumped.

Fernet is used because it authenticates: a tampered ciphertext fails loudly
instead of decrypting to garbage that then gets rendered as a customer's name.
It is deliberately randomised, so two rows holding the same phone number produce
different ciphertext. That defeats the "count identical ciphertexts to find your
biggest customer" attack, and costs nothing here because grouping never touches
these columns - it uses the hash.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)

ENV_KEY = "PII_ENCRYPTION_KEY"


class PiiKeyMissing(RuntimeError):
    """No key configured. Encrypting or decrypting must fail, never fall back."""


@lru_cache(maxsize=1)
def _fernet():
    """Build the cipher once. Cached because key derivation is not free."""
    from cryptography.fernet import Fernet

    key = os.environ.get(ENV_KEY, "").strip()
    if not key:
        raise PiiKeyMissing(
            f"Falta la variable de entorno {ENV_KEY}. Sin ella no se pueden "
            "guardar ni leer los datos de contacto del cliente. Genera una con: "
            "python -m scripts.generate_pii_key"
        )
    try:
        return Fernet(key.encode())
    except Exception as exc:  # malformed key: wrong length, not base64, truncated
        raise PiiKeyMissing(
            f"{ENV_KEY} no es una llave válida de Fernet (32 bytes en base64 "
            "urlsafe). Genera una con: python -m scripts.generate_pii_key"
        ) from exc


def pii_available() -> bool:
    """True when contact data can be read. Lets callers degrade instead of 500."""
    try:
        _fernet()
    except PiiKeyMissing:
        return False
    return True


def encrypt_pii(value: str | None) -> bytes | None:
    """Encrypt one field. None and blank stay None - never encrypt emptiness."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _fernet().encrypt(text.encode("utf-8"))


def decrypt_pii(blob: bytes | memoryview | None) -> str | None:
    """Decrypt one field, or None if there is nothing readable there.

    A value that fails to decrypt is NOT an exception the caller has to handle:
    it means the row was written under a different key, which happens after a
    rotation and must not take down a whole page of orders. It logs and reads as
    absent, exactly like a row that never had a phone number.
    """
    if blob is None:
        return None
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(bytes(blob)).decode("utf-8")
    except InvalidToken:
        logger.warning("PII value could not be decrypted; wrong or rotated key")
        return None
    except PiiKeyMissing:
        raise


def generate_key() -> str:
    """A fresh key, for `scripts.generate_pii_key` and for tests."""
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()
