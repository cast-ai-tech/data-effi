"""Parsing and normalization primitives.

LATAM COD reports arrive from a dozen tools with no agreed conventions: money is
written `1.234.567` in Colombia and `1,234.56` in Mexico, dates are dd/mm/yyyy
except when a spreadsheet already parsed them, and city names carry accents in
one file and not in the next. Everything that turns messy text into a typed value
lives here so the rules stay testable and identical across every reader.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_WHITESPACE_RE = re.compile(r"\s+")
_NON_DIGIT_RE = re.compile(r"[^\d]")
_MONEY_ALLOWED_RE = re.compile(r"[^\d,.\-]")


def normalize_text(value: Any) -> str | None:
    """Lowercase, strip accents, collapse whitespace.

    Mirrors core.normalize_text() in SQL. If you change one, change the other:
    dimension lookups depend on both producing the same key.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    collapsed = _WHITESPACE_RE.sub(" ", stripped).strip().lower()
    return collapsed or None


def clean_text(value: Any) -> str | None:
    """Trim and collapse whitespace, preserving the original casing and accents."""
    if value is None:
        return None
    text = _WHITESPACE_RE.sub(" ", str(value)).strip()
    return text or None


def parse_decimal(value: Any) -> Decimal | None:
    """Parse money written in any LATAM convention.

    Rule: when both separators appear, the RIGHTMOST one is the decimal
    separator. When only one appears, it is a decimal separator only if exactly
    one occurrence is followed by one or two digits.

        parse_decimal("1.234.567")   -> 1234567
        parse_decimal("1,234.56")    -> 1234.56
        parse_decimal("1.234,56")    -> 1234.56
        parse_decimal("$ 89.900")    -> 89900
        parse_decimal("(1.200)")     -> -1200
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return Decimal(str(value))

    text = str(value).strip()
    if not text:
        return None

    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]

    text = _MONEY_ALLOWED_RE.sub("", text)
    if not text or text in {"-", ".", ","}:
        return None
    if text.startswith("-"):
        negative = True
        text = text[1:]

    last_dot = text.rfind(".")
    last_comma = text.rfind(",")

    if last_dot >= 0 and last_comma >= 0:
        dec_pos = max(last_dot, last_comma)
        integer_part = _NON_DIGIT_RE.sub("", text[:dec_pos])
        decimal_part = _NON_DIGIT_RE.sub("", text[dec_pos + 1:])
    elif last_dot >= 0 or last_comma >= 0:
        sep = "." if last_dot >= 0 else ","
        pos = last_dot if last_dot >= 0 else last_comma
        tail = text[pos + 1:]
        is_decimal = text.count(sep) == 1 and 1 <= len(tail) <= 2 and tail.isdigit()
        if is_decimal:
            integer_part = _NON_DIGIT_RE.sub("", text[:pos])
            decimal_part = tail
        else:
            integer_part = _NON_DIGIT_RE.sub("", text)
            decimal_part = ""
    else:
        integer_part = _NON_DIGIT_RE.sub("", text)
        decimal_part = ""

    if not integer_part and not decimal_part:
        return None

    # Build without a trailing ".0" when there is no fractional part, so that
    # str(Decimal) round-trips as the user wrote it. Discrepancy records compare
    # these as text: "149900" and "149900.0" must not look like a change.
    candidate = f"{integer_part or '0'}.{decimal_part}" if decimal_part else (integer_part or "0")
    try:
        amount = Decimal(candidate)
    except InvalidOperation:
        return None
    return -amount if negative else amount


def parse_int(value: Any, default: int | None = None) -> int | None:
    parsed = parse_decimal(value)
    if parsed is None:
        return default
    return int(parsed)


_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d/%m/%y",
    "%Y/%m/%d",
    "%m/%d/%Y",          # last resort; ambiguous dates prefer dd/mm above
)

_DATETIME_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
)


def parse_datetime(value: Any) -> datetime | None:
    """Parse a timestamp from a spreadsheet cell or a string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)

    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "").split(".")[0].strip()

    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    parsed_date = parse_date(text)
    return datetime(parsed_date.year, parsed_date.month, parsed_date.day) if parsed_date else None


def parse_date(value: Any) -> date | None:
    """Parse a date. dd/mm/yyyy wins over mm/dd/yyyy - this is LATAM."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text:
        return None
    text = text.split(" ")[0].split("T")[0].strip()

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


_PHONE_KEEP_RE = re.compile(r"[^\d]")


def hash_customer(raw_identifier: Any, salt: str) -> str | None:
    """Hash a customer phone or document. THE ONLY thing we keep about a person.

    Digits are extracted first so that `+57 300 123 4567`, `300-123-4567` and
    `573001234567` all collapse to the same identity. A local Colombian number
    written without the country code stays distinct from the same number written
    with it - that is accepted: a false split is harmless, a false merge is not.

    The salt is per-tenant and lives in the environment. Never log either input.
    """
    if raw_identifier is None or not salt:
        return None
    digits = _PHONE_KEEP_RE.sub("", str(raw_identifier))
    if len(digits) < 6:
        return None
    return hashlib.sha256(f"{salt}:{digits}".encode()).hexdigest()


def content_hash(payload: bytes) -> str:
    """SHA-256 of the raw file bytes. The idempotency key for a load."""
    return hashlib.sha256(payload).hexdigest()


def dedupe_key(*parts: Any) -> str:
    """Stable hash of the fields that identify a movement or an ad-spend row."""
    joined = "|".join("" if p is None else str(p).strip().lower() for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def normalize_tracking(value: Any) -> str | None:
    """Tracking numbers arrive with spaces, dashes and stray quotes."""
    if value is None:
        return None
    text = str(value).strip().strip("'\"").replace(" ", "")
    text = text.rstrip(".0") if re.fullmatch(r"\d+\.0+", text) else text
    return text.upper() or None


def normalize_currency(value: Any, default: str) -> str:
    text = clean_text(value)
    if text and len(text) == 3 and text.isalpha():
        return text.upper()
    return default.upper()
