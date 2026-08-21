"""Contact data: who may receive it, and the record that they did.

THE RULE THIS MODULE EXISTS TO ENFORCE
--------------------------------------
A `viewer` never receives a customer's name, phone, address or document. Not
greyed out, not hidden behind a CSS class, not present-but-masked: absent from
the JSON the server builds. A field the client is told to hide is a field the
client can un-hide, and every response is one `curl` away from being read
without a browser.

So the decision is made once, here, before a row is turned into a response
model, and the router asks this module rather than deciding for itself.

TWO INDEPENDENT GATES
---------------------
    role   - owner and analyst may read contact data; viewer may not.
    key    - with no PII_ENCRYPTION_KEY there is nothing to decrypt.

Both have to open. The second one is what keeps a deployment that has not
configured the key from returning 500 on the orders page: the page renders,
the contact columns read as empty, and `pii_visible` tells the UI to say why
instead of implying those customers have no phone number.

WHAT IS LOGGED
--------------
The fact, never the value. `log_access` writes who, when, which endpoint and how
many records - and nothing else. A decrypted value must never reach a log line,
an error message, or a traceback: `decrypt_pii` already swallows a bad token
rather than raising with the ciphertext attached, and nothing here puts a
plaintext into a format string.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg

from api.db import execute
from api.deps import CurrentUser
from pipeline.crypto import decrypt_pii, pii_available

logger = logging.getLogger(__name__)

# Response field -> the ciphertext column it is decrypted from.
#
# `mart.v_orders` carries all four; `mart.v_customer_metrics` carries the two
# from the customer's most recent guide under different names. The mapping is
# written here so a router can never name a column that is not contact data,
# and never miss one that is.
ORDER_CONTACT: dict[str, str] = {
    "customer_name": "customer_name_enc",
    "customer_phone": "customer_phone_enc",
}

ORDER_DETAIL_CONTACT: dict[str, str] = {
    **ORDER_CONTACT,
    "customer_address": "customer_address_enc",
    "customer_document": "customer_document_enc",
}

CUSTOMER_CONTACT: dict[str, str] = {
    "customer_name": "last_name_enc",
    "customer_phone": "last_phone_enc",
}

# The customers TABLE uses CUSTOMER_CONTACT; only the detail card uses this.
# Keeping them as two mappings is what makes "the list cannot return an
# address" a property of the code rather than a thing to remember.
CUSTOMER_DETAIL_CONTACT: dict[str, str] = {
    **CUSTOMER_CONTACT,
    "customer_document": "last_document_enc",
    "customer_address": "last_address_enc",
}


def contact_visible(user: CurrentUser) -> bool:
    """True when this caller may receive decrypted contact data."""
    return user.at_least("analyst") and pii_available()


def reveal(
    row: dict[str, Any], mapping: dict[str, str], *, visible: bool
) -> dict[str, str | None]:
    """Turn ciphertext columns into readable fields, or into nulls.

    Returns the same keys either way. That matters: a response whose shape
    changes with the caller's role teaches the client to guess, and a client
    that guesses eventually guesses that a missing key means "not loaded yet"
    and retries until it gets one.
    """
    if not visible:
        return dict.fromkeys(mapping)
    return {field: decrypt_pii(row.get(column)) for field, column in mapping.items()}


def carried_contact(contact: dict[str, str | None]) -> bool:
    """Did this record actually disclose anything? Drives the logged count."""
    return any(value is not None for value in contact.values())


def log_access(
    conn: psycopg.Connection,
    user: CurrentUser,
    *,
    endpoint: str,
    record_count: int,
    ip: str | None,
) -> None:
    """Record that contact data left the server. Never what it was.

    Runs on the request's own connection, so it lands in the same transaction as
    the read: a request that fails after this point logs nothing, because
    nothing was ever sent. `endpoint` is the route template - a rendered URL
    carries the operator's search term, which is frequently a phone number.

    A count of zero is not logged: the response existed but disclosed nothing,
    and an audit trail full of empty reads is one nobody scrolls through.
    """
    if record_count <= 0:
        return

    try:
        execute(
            conn,
            """
            INSERT INTO raw.pii_access (tenant_id, user_id, endpoint, record_count, ip)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user.tenant_id, user.id, endpoint, record_count, ip),
        )
    except psycopg.Error:
        # The read already succeeded and the data is already in the response.
        # Failing the request now would not un-disclose it - it would only cost
        # the operator their page. Log loudly instead; a gap in the trail is a
        # thing to alert on, not a thing to hide.
        logger.exception(
            "could not record PII access: tenant=%s user=%s endpoint=%s records=%s",
            user.tenant_id, user.id, endpoint, record_count,
        )
