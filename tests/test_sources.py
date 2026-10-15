"""CSV connector: file checks, typed parsing, normalization, rejection reasons and duplicates."""

from __future__ import annotations

import pytest

from reporting_pipeline.errors import SourceError
from reporting_pipeline.sources import SPECS, RowError, iso_date, money_cents, one_of, rate_basis_points, read_source

ORDERS_HEADER = "order_id,order_date,customer_id,customer_segment,region,sales_channel,order_status\n"
LINES_HEADER = "order_id,line_number,product_id,quantity,unit_price,discount_rate\n"


def _write(tmp_path, name, text, encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


@pytest.mark.parametrize("text, cents", [("1049.00", 104900), ("0.5", 50), ("7", 700), ("12.34", 1234)])
def test_money_is_parsed_exactly_to_cents(text, cents):
    assert money_cents(text) == cents


@pytest.mark.parametrize(
    "text, reason",
    [
        ("12,50", "invalid_number"),
        ("1e3", "invalid_number"),
        ("$5", "invalid_number"),
        ("1.234", "invalid_number"),
        ("-3.00", "out_of_range"),
    ],
)
def test_invalid_money_has_a_reason(text, reason):
    with pytest.raises(RowError) as info:
        money_cents(text)
    assert info.value.reason == reason


def test_rates_dates_and_allowed_values():
    assert rate_basis_points("0.15") == 1500 and rate_basis_points("1") == 10000 and rate_basis_points("0.0125") == 125
    for bad, reason in (("1.50", "out_of_range"), ("15%", "invalid_number"), ("0.12345", "invalid_number")):
        with pytest.raises(RowError) as info:
            rate_basis_points(bad)
        assert info.value.reason == reason
    assert iso_date("2024-02-29") == "2024-02-29"
    for bad in ("2025-02-29", "2025-02-30", "30/09/2026", "2026-9-1"):
        with pytest.raises(RowError, match="date"):
            iso_date(bad)
    channel = one_of("Online", "Store")
    assert channel("online") == "Online" and channel("STORE") == "Store"
    with pytest.raises(RowError, match="not one of"):
        channel("Fax")


def test_rows_are_accepted_normalized_or_rejected_with_line_and_reason(tmp_path):
    path = _write(
        tmp_path,
        "orders.csv",
        ORDERS_HEADER
        + "SO-1,2026-01-05,C1,consumer , north,Online,completed\n"  # normalized, kept
        + "SO-2,2026-01-06,C2,Consumer,North,Online,shipped\n"  # invalid_value
        + "SO-3,,C3,Consumer,North,Online,completed\n"  # missing_value
        + "SO-4,2026-01-07,C4,Consumer\n"  # malformed_row
        + "\n",
    )
    result = read_source(SPECS["orders"], path)
    assert result.rows_read == 4
    assert [r["customer_segment"] for r in result.accepted] == ["Consumer"]
    assert result.accepted[0]["region"] == "North" and result.normalized_values == 2
    assert [(r.line, r.reason, r.column) for r in result.rejected] == [
        (3, "invalid_value", "order_status"),
        (4, "missing_value", "order_date"),
        (5, "malformed_row", None),
    ]
    assert result.rejected[0].raw["order_id"] == "SO-2"
    assert any("blank line" in w for w in result.warnings)


def test_exact_duplicates_keep_the_first_conflicts_reject_every_copy(tmp_path):
    path = _write(
        tmp_path,
        "lines.csv",
        LINES_HEADER
        + "SO-1,1,P1,2,10.00,0.00\n"
        + "SO-1,1,P1,2,10.00,0.00\n"  # exact copy -> duplicate_row
        + "SO-2,1,P1,1,10.00,0.00\n"
        + "SO-2,1,P2,1,10.00,0.00\n",
    )  # same key, other product -> both rejected
    result = read_source(SPECS["order_lines"], path)
    assert [(r["order_id"], r["source_line"]) for r in result.accepted] == [("SO-1", 2)]
    assert [(r.line, r.reason) for r in result.rejected] == [
        (3, "duplicate_row"),
        (4, "duplicate_key_conflict"),
        (5, "duplicate_key_conflict"),
    ]
    assert "exact copy of line 2" in result.rejected[0].message


@pytest.mark.parametrize(
    "content, message",
    [
        ("", "is empty"),
        ("order_id,line_number\nSO-1,1\n", "missing required column"),
        (LINES_HEADER.replace("quantity", "order_id"), "duplicate column name"),
        (LINES_HEADER + 'SO-1,1,"P1,2,10.00,0.00\n', "not valid CSV"),
    ],
)
def test_unusable_files_raise_source_errors(tmp_path, content, message):
    path = _write(tmp_path, "lines.csv", content)
    with pytest.raises(SourceError, match=message):
        read_source(SPECS["order_lines"], path)


def test_missing_file_encoding_and_bom(tmp_path):
    with pytest.raises(SourceError, match="not found"):
        read_source(SPECS["order_lines"], tmp_path / "absent.csv")
    latin = _write(
        tmp_path,
        "latin.csv",
        "product_id,product_name,category,unit_cost,list_price,launch_date\n"
        "P1,Chaise café,Furniture,1.00,2.00,2024-01-01\n",
        encoding="cp1252",
    )
    with pytest.raises(SourceError, match="not valid utf-8"):
        read_source(SPECS["products"], latin)
    assert read_source(SPECS["products"], latin, encoding="cp1252").accepted[0]["product_name"] == "Chaise café"
    bom = _write(tmp_path, "bom.csv", "﻿" + LINES_HEADER + "SO-1,1,P1,1,1.00,0.00\n")
    assert len(read_source(SPECS["order_lines"], bom).accepted) == 1


def test_extra_columns_are_ignored_with_a_warning(tmp_path):
    path = _write(tmp_path, "lines.csv", LINES_HEADER.strip() + ",comment\nSO-1,1,P1,1,1.00,0.00,hello\n")
    result = read_source(SPECS["order_lines"], path)
    assert len(result.accepted) == 1 and "comment" in result.warnings[0]
