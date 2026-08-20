"""File readers: bytes in, (headers, rows) out.

Deliberately dumb. No business logic, no type coercion beyond what the file
format itself already decided (a spreadsheet cell that is a real date stays a
date). Everything else is normalize.py's job.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from typing import Any

SUPPORTED_EXTENSIONS = (".csv", ".xlsx", ".xlsm", ".txt", ".tsv")

_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
_MAX_PREVIEW_BYTES = 64 * 1024


class UnsupportedFileError(ValueError):
    """The file is not something Norte knows how to read."""


class EmptyFileError(ValueError):
    """The file parsed fine but carries no usable rows."""


def read_tabular(payload: bytes, filename: str) -> tuple[list[str], list[list[Any]]]:
    """Read a CSV or XLSX file into a header list and a list of row values."""
    lowered = filename.lower()
    if lowered.endswith((".xlsx", ".xlsm")):
        headers, rows = _read_xlsx(payload)
    elif lowered.endswith((".csv", ".txt", ".tsv")):
        headers, rows = _read_csv(payload)
    else:
        raise UnsupportedFileError(
            f"Formato no soportado: {filename}. Se aceptan {', '.join(SUPPORTED_EXTENSIONS)}"
        )

    if not headers:
        raise EmptyFileError(f"{filename}: no se encontró una fila de encabezados")
    if not rows:
        raise EmptyFileError(f"{filename}: el archivo no tiene filas de datos")
    return headers, rows


def _decode(payload: bytes) -> str:
    last_error: Exception | None = None
    for encoding in _ENCODINGS:
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    raise UnsupportedFileError(f"No se pudo decodificar el archivo: {last_error}")


def _sniff_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        # Fall back to whichever candidate appears most in the first lines.
        counts = {sep: sample.count(sep) for sep in (";", ",", "\t", "|")}
        return max(counts, key=counts.get) if any(counts.values()) else ","


def _read_csv(payload: bytes) -> tuple[list[str], list[list[Any]]]:
    text = _decode(payload)
    delimiter = _sniff_delimiter(text[:_MAX_PREVIEW_BYTES])
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)

    headers: list[str] = []
    rows: list[list[Any]] = []
    for raw_row in reader:
        if not headers:
            if _is_blank(raw_row):
                continue                    # skip leading title/blank lines
            headers = [str(cell).strip() for cell in raw_row]
            continue
        if _is_blank(raw_row):
            continue
        rows.append(list(raw_row))
    return headers, rows


def _read_xlsx(payload: bytes) -> tuple[list[str], list[list[Any]]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:      # pragma: no cover - dependency is declared
        raise UnsupportedFileError(
            "Falta la dependencia openpyxl para leer archivos Excel"
        ) from exc

    workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        headers: list[str] = []
        rows: list[list[Any]] = []
        for raw_row in sheet.iter_rows(values_only=True):
            values = list(raw_row)
            if not headers:
                if _is_blank(values):
                    continue
                headers = [("" if cell is None else str(cell).strip()) for cell in values]
                continue
            if _is_blank(values):
                continue
            rows.append(values)
        return headers, rows
    finally:
        workbook.close()


def _is_blank(row: list[Any]) -> bool:
    return all(cell is None or str(cell).strip() == "" for cell in row)


def iter_records(
    headers: list[str], rows: list[list[Any]], header_map: dict[int, str]
) -> Iterator[tuple[int, dict[str, Any], dict[str, Any]]]:
    """Yield (row_number, mapped_fields, raw_payload) for each data row.

    row_number is 1-based over data rows (the header is row 0), which is what the
    error report shows the user. raw_payload keeps the whole original row so it
    can be stored in raw.source_row for audit.
    """
    for index, row in enumerate(rows, start=1):
        mapped: dict[str, Any] = {}
        raw: dict[str, Any] = {}
        for position, value in enumerate(row):
            header = headers[position] if position < len(headers) else f"col_{position}"
            raw[header] = None if value is None else str(value)
            field_name = header_map.get(position)
            if field_name is not None:
                mapped[field_name] = value
        yield index, mapped, raw
