"""The copilot's rows are cut to the caller's countries BEFORE the prose.

`ai.features.cut_rows_to_countries` is what stands between a limited
membership and a generated query that ignored the country hint. Pure function,
no database, no model.
"""

from __future__ import annotations

from ai.features import cut_rows_to_countries

SCOPE = ("GT", "HN")


def test_rows_from_other_countries_are_dropped():
    rows = [
        {"country_code": "GT", "shipments": 10},
        {"country_code": "cr", "shipments": 99},
        {"country_code": "HN", "shipments": 5},
        {"country_code": None, "shipments": 1},
    ]
    kept, columns, note = cut_rows_to_countries(rows, ["country_code", "shipments"], SCOPE)
    assert note is None
    assert columns == ["country_code", "shipments"]
    assert [row["country_code"] for row in kept] == ["GT", "HN"]


def test_a_result_without_a_country_column_is_not_handed_over():
    rows = [{"shipments": 120, "delivered": 90}]
    kept, _, note = cut_rows_to_countries(rows, ["shipments", "delivered"], SCOPE)
    assert kept == []
    assert note is not None
    assert "GT, HN" in note


def test_an_empty_result_stays_empty_and_quiet():
    kept, _, note = cut_rows_to_countries([], ["country_code", "shipments"], SCOPE)
    assert kept == [] and note is None
