"""Contact data on the way in: hashed for identity, encrypted for display.

The design these tests defend is that ONE phone number produces TWO stored
values with opposite properties, and that neither can do the other's job:

    customer_hash      same input -> same output, forever, and no way back
    customer_phone_enc same input -> different output every time, reversible

Everything below exists because getting one of those backwards is a real
failure with no error message. A deterministic ciphertext leaks the customer
list by frequency analysis. A non-deterministic hash splits one customer into
one row per upload and every customer metric quietly becomes wrong.
"""

from __future__ import annotations

import pytest

from pipeline import crypto
from pipeline.crypto import decrypt_pii
from pipeline.ingest import PII_KEY_MISSING_WARNING, IngestEngine, MemoryStore
from pipeline.models import BatchKind
from tests.conftest import CONNECTION_ID, COUNTRY, CURRENCY, PLATFORM, TENANT_ID, TODAY

# The columns are Effi's, verbatim, because the profile matches on exact names.
# Only the ones this file is about are here; the profile needs no more than its
# four signature columns to recognise the report.
HEADERS = (
    "Guía transportadora;Prefijo ID guía;Fecha de envío;Estado global guía inicial;"
    "Nombre transportadora Efficommerce;Valor recaudo;Destinatario;"
    "Teléfonos destinatario;ID. destinatario;Dirección destinatario;"
    "Ciudad destinatario;País destinatario;Contenido"
)

# Two guides, ONE customer: the same phone written two different ways, which is
# what a real export looks like when the same person orders twice.
GUIDES_DAY_ONE = f"""{HEADERS}
G-0001;EF-1;01/07/2026;Entregado;Interrapidisimo;89900;Juana Ficticia;3001234567;1012345678;Calle 45 # 12-34 Apto 501;Bogotá;Colombia;1 * FAJA REDUCTORA.
G-0002;EF-2;05/07/2026;En transito;Interrapidisimo;120000;Juana Ficticia;300 123 4567;1012345678;Calle 45 # 12-34 Apto 501;Bogotá;Colombia;2 * CLOROFILA.
G-0003;EF-3;06/07/2026;En transito;Servientrega;45000;Pedro Ficticio;3109876543;;;Medellín;Colombia;1 * ZOOONE.
""".encode()

# The same three guides one day later: G-0001 has moved on, and G-0003 now
# carries the address and document that were blank the first time.
GUIDES_DAY_TWO = f"""{HEADERS}
G-0001;EF-1;01/07/2026;Entregado;Interrapidisimo;89900;Juana Ficticia;3001234567;1012345678;Calle 45 # 12-34 Apto 501;Bogotá;Colombia;1 * FAJA REDUCTORA.
G-0002;EF-2;05/07/2026;Entregado;Interrapidisimo;120000;Juana Ficticia;300 123 4567;1012345678;Calle 45 # 12-34 Apto 501;Bogotá;Colombia;2 * CLOROFILA.
G-0003;EF-3;06/07/2026;Entregado;Servientrega;45000;Pedro Ficticio;3109876543;71234567;Carrera 70 # 30-15;Medellín;Colombia;1 * ZOOONE.
""".encode()


@pytest.fixture
def pii_key(monkeypatch):
    """A throwaway key, live for one test only.

    `crypto._fernet` is lru_cached, so a key set after another test already
    built a cipher would be ignored. Clearing it on both sides is what keeps
    the with-key and without-key tests from depending on their order.
    """
    monkeypatch.setenv(crypto.ENV_KEY, crypto.generate_key())
    crypto._fernet.cache_clear()
    yield
    crypto._fernet.cache_clear()


@pytest.fixture
def no_pii_key(monkeypatch):
    """The deployment that has not configured a key yet."""
    monkeypatch.delenv(crypto.ENV_KEY, raising=False)
    crypto._fernet.cache_clear()
    yield
    crypto._fernet.cache_clear()


def _ingest(engine, payload, name):
    return engine.ingest(
        payload=payload,
        source_name=name,
        kind=BatchKind.SHIPMENTS,
        tenant_id=TENANT_ID,
        connection_id=CONNECTION_ID,
        country_code=COUNTRY,
        platform_code=PLATFORM,
        default_currency=CURRENCY,
    )


def _load(payload, pii_salt, name="guias.csv", store=None):
    """One file through a fresh engine. Returns (store, report)."""
    store = store if store is not None else MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)
    return store, _ingest(engine, payload, name)


def _shipment(store, tracking):
    return store.shipments[(CONNECTION_ID, tracking)]


# =============================================================================
# The contact data comes back out
# =============================================================================


def test_every_contact_field_survives_the_round_trip(pii_key, pii_salt):
    """What the operator reads has to be what the guide said."""
    store, report = _load(GUIDES_DAY_ONE, pii_salt)
    assert report.rows_failed == 0

    guide = _shipment(store, "G-0001")
    assert decrypt_pii(guide.customer_name_enc) == "Juana Ficticia"
    assert decrypt_pii(guide.customer_phone_enc) == "3001234567"
    assert decrypt_pii(guide.customer_document_enc) == "1012345678"
    assert decrypt_pii(guide.customer_address_enc) == "Calle 45 # 12-34 Apto 501"


def test_the_city_is_stored_readable_because_a_city_is_not_a_person(pii_key, pii_salt):
    """Geographic metrics need to read it, and it identifies nobody alone."""
    store, _ = _load(GUIDES_DAY_ONE, pii_salt)

    assert _shipment(store, "G-0001").customer_city_name == "Bogotá"
    assert _shipment(store, "G-0003").customer_city_name == "Medellín"


def test_a_blank_contact_column_stays_null_rather_than_encrypting_emptiness(
    pii_key, pii_salt
):
    """An encrypted empty string is indistinguishable from a real value.

    It would render as a present-but-blank field in the orders table and hide
    the fact that the export never carried the data.
    """
    guide = _shipment(_load(GUIDES_DAY_ONE, pii_salt)[0], "G-0003")

    assert guide.customer_document_enc is None
    assert guide.customer_address_enc is None
    assert guide.customer_name_enc is not None      # this one WAS in the file


# =============================================================================
# The heart of the design: one identity, two representations
# =============================================================================


def test_one_phone_gives_one_hash_but_two_different_ciphertexts(pii_key, pii_salt):
    """Grouping and confidentiality at the same time, from the same column.

    The hash must match so both guides belong to one customer. The ciphertext
    must NOT match, or anyone with read access could count identical values and
    rank the customer list without decrypting anything.
    """
    store, _ = _load(GUIDES_DAY_ONE, pii_salt)

    first = _shipment(store, "G-0001")
    second = _shipment(store, "G-0002")

    # Same person: the hash ignores how the phone was punctuated.
    assert first.customer_hash == second.customer_hash
    assert first.customer_hash is not None

    # Same value, different bytes.
    assert first.customer_phone_enc != second.customer_phone_enc
    assert decrypt_pii(first.customer_phone_enc) == "3001234567"
    assert decrypt_pii(second.customer_phone_enc) == "300 123 4567"

    # And a different person is a different customer.
    assert _shipment(store, "G-0003").customer_hash != first.customer_hash


def test_the_ciphertext_carries_no_trace_of_what_it_encrypts(pii_key, pii_salt):
    store, _ = _load(GUIDES_DAY_ONE, pii_salt)
    guide = _shipment(store, "G-0001")

    blob = b"".join(
        column
        for column in (
            guide.customer_name_enc,
            guide.customer_phone_enc,
            guide.customer_document_enc,
            guide.customer_address_enc,
        )
        if column
    )
    for secret in (b"Juana", b"3001234567", b"1012345678", b"Calle 45"):
        assert secret not in blob


# =============================================================================
# The raw archive is a different problem and keeps its own answer
# =============================================================================


def test_the_raw_archive_still_holds_hashes_and_never_ciphertext(pii_key, pii_salt):
    """Mapping a column for encryption must not change what the archive keeps.

    The archive is a whole-file dump for auditing a load; auditing never needs
    to know whose guide it was, so its PII stays one-way. If this ever flips,
    the encryption key becomes the only thing standing between a backup and
    every customer in it.
    """
    store, report = _load(GUIDES_DAY_ONE, pii_salt)
    row = store.source_rows[report.batch_id][0]

    for header in (
        "Destinatario",
        "Teléfonos destinatario",
        "ID. destinatario",
        "Dirección destinatario",
    ):
        assert header in row.redacted_fields
        assert str(row.payload[header]).startswith("sha256:")

    # No plaintext, and no bytes either: a bytea in a JSON archive would mean
    # the ciphertext got copied where the hash belongs.
    archived = repr(row.payload)
    assert "Juana Ficticia" not in archived
    assert "3001234567" not in archived
    assert not any(isinstance(value, bytes) for value in row.payload.values())

    # The city is not PII and is archived as written.
    assert row.payload["Ciudad destinatario"] == "Bogotá"


# =============================================================================
# Reloading must not churn the ciphertext
# =============================================================================


def test_a_second_file_leaves_the_stored_ciphertext_byte_for_byte_identical(
    pii_key, pii_salt
):
    """Encryption is randomised, so rewriting it on every load would mean every
    row looks changed on every load. The contact columns fill a gap once and are
    then left alone, exactly like the city or the carrier.
    """
    store = MemoryStore()
    _load(GUIDES_DAY_ONE, pii_salt, "dia1.csv", store=store)
    before = {
        tracking: _shipment(store, tracking).customer_phone_enc
        for tracking in ("G-0001", "G-0002", "G-0003")
    }

    # A different file (different bytes, so it is not skipped as a duplicate)
    # carrying the same guides with a status that has moved on.
    _, report = _load(GUIDES_DAY_TWO, pii_salt, "dia2.csv", store=store)
    assert report.already_loaded is False
    assert report.rows_updated > 0          # the statuses did advance

    for tracking, ciphertext in before.items():
        assert _shipment(store, tracking).customer_phone_enc == ciphertext


def test_contact_data_missing_the_first_time_is_filled_in_the_second(
    pii_key, pii_salt
):
    """"Never overwrite" is not "never write": a gap is still a gap."""
    store = MemoryStore()
    _load(GUIDES_DAY_ONE, pii_salt, "dia1.csv", store=store)
    assert _shipment(store, "G-0003").customer_address_enc is None

    _load(GUIDES_DAY_TWO, pii_salt, "dia2.csv", store=store)

    filled = _shipment(store, "G-0003")
    assert decrypt_pii(filled.customer_address_enc) == "Carrera 70 # 30-15"
    assert decrypt_pii(filled.customer_document_enc) == "71234567"


# =============================================================================
# No key: degrade, do not fail
# =============================================================================


def test_without_a_key_the_load_still_happens_with_hashes_only(no_pii_key, pii_salt):
    """A setting nobody configured must not cost the operator their metrics.

    Every number in the dashboard comes from the guide, not from the customer's
    name. Refusing the file would trade all of them for contact data that can be
    backfilled the moment a key exists.
    """
    store, report = _load(GUIDES_DAY_ONE, pii_salt)

    assert report.rows_failed == 0
    assert report.rows_inserted == 3

    guide = _shipment(store, "G-0001")
    assert guide.customer_hash is not None          # identity survives
    assert guide.customer_name_enc is None
    assert guide.customer_phone_enc is None
    assert guide.customer_document_enc is None
    assert guide.customer_address_enc is None
    assert guide.customer_city_name == "Bogotá"     # never needed a key


def test_without_a_key_the_batch_report_says_so_once(no_pii_key, pii_salt):
    """Once for the batch, not once per row: 3 rows here, 1,649 in a real file."""
    _, report = _load(GUIDES_DAY_ONE, pii_salt)

    assert report.warnings == [PII_KEY_MISSING_WARNING]
    assert "PII_ENCRYPTION_KEY" in report.warnings[0]
    assert report.to_json()["warnings"] == [PII_KEY_MISSING_WARNING]

    # It is a batch-wide degradation, not a per-row defect.
    assert all(issue.code != "pii_not_encrypted" for issue in report.sanity_issues)


def test_with_a_key_there_is_nothing_to_warn_about(pii_key, pii_salt):
    _, report = _load(GUIDES_DAY_ONE, pii_salt)
    assert report.warnings == []


def test_a_key_added_later_encrypts_the_guides_that_were_loaded_without_one(
    monkeypatch, pii_salt
):
    """The degradation is recoverable, which is what makes it acceptable.

    A guide stored with NULL contact columns has a gap, and a gap is what the
    static merge rule is allowed to fill - so the next upload carrying that
    guide backfills it.
    """
    store = MemoryStore()

    monkeypatch.delenv(crypto.ENV_KEY, raising=False)
    crypto._fernet.cache_clear()
    _load(GUIDES_DAY_ONE, pii_salt, "dia1.csv", store=store)
    assert _shipment(store, "G-0001").customer_name_enc is None

    monkeypatch.setenv(crypto.ENV_KEY, crypto.generate_key())
    crypto._fernet.cache_clear()
    try:
        _load(GUIDES_DAY_TWO, pii_salt, "dia2.csv", store=store)
        assert decrypt_pii(_shipment(store, "G-0001").customer_name_enc) == "Juana Ficticia"
    finally:
        crypto._fernet.cache_clear()
