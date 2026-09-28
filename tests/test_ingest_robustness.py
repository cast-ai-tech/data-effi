"""Regresiones de la auditoría del motor de ingesta (rama audit/pipeline-worker).

Cada prueba fija un error real que hacía que un número del tablero saliera mal
sin que nadie se enterara.
"""

from __future__ import annotations

import pytest

from pipeline.normalize import normalize_tracking


# =============================================================================
# Números de guía
# =============================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # El sufijo ".0" de Excel se va; los ceros de la guía se quedan.
        ("240012345670.0", "240012345670"),
        ("1000.00", "1000"),
        ("100", "100"),
        # Celda numérica leída como float, incluida la que str() pondría en
        # notación científica.
        (240012345670.0, "240012345670"),
        (1e15, "1000000000000000"),
        (12345, "12345"),
        (" 'abc-10' ", "ABC-10"),
        (None, None),
        ("", None),
    ],
)
def test_tracking_keeps_its_trailing_zeros(raw, expected):
    assert normalize_tracking(raw) == expected
