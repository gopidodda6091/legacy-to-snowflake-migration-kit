from __future__ import annotations

import copy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from migrate import extract as ex
from migrate import load as ld
from migrate import reconcile as rc
from migrate.cli import main
from migrate.translate import TableDef

ROOT = Path(__file__).resolve().parents[1]


def _result(table: TableDef, source: list[dict], target: list[dict]) -> rc.TableResult:
    return rc.compare(table, rc.profile_source(table, source), rc.profile_source(table, target))


# --- normalisation ----------------------------------------------------------------


@pytest.mark.parametrize(
    "a, b, typ",
    [
        ("1.50", Decimal("1.5000"), "NUMBER(10,4)"),
        ("0.00", Decimal("0"), "NUMBER(10,2)"),
        ("100", Decimal("1E+2"), "NUMBER(10,0)"),
        ("2026-03-01T08:00:00-06:00", datetime(2026, 3, 1, 14, tzinfo=UTC), "TIMESTAMP_TZ(6)"),
        ("2026-03-01 08:00:00", datetime(2026, 3, 1, 8), "TIMESTAMP_NTZ(0)"),
        ({"b": 1, "a": [1, 2]}, '{"a": [1, 2], "b": 1}', "VARIANT"),
        ("00FF", b"\x00\xff", "BINARY"),
        (0.1 + 0.2, 0.3, "FLOAT"),
    ],
)
def test_equivalent_representations_normalise_equal(a, b, typ: str) -> None:
    assert rc.normalize(a, typ) == rc.normalize(b, typ)


def test_timestamp_tz_without_offset_is_rejected() -> None:
    with pytest.raises(ValueError):
        rc.normalize("2026-03-01 08:00:00", "TIMESTAMP_TZ(6)")


# --- edge cases ---------------------------------------------------------------------


def test_identical_tables_pass(sales_table: TableDef, sales_rows) -> None:
    assert _result(sales_table, sales_rows, list(reversed(sales_rows))).passed


def test_empty_tables_pass(sales_table: TableDef) -> None:
    result = _result(sales_table, [], [])
    assert result.passed and result.source_rows == 0


def test_null_vs_empty_string_fails(erp_table: TableDef, erp_rows) -> None:
    target = copy.deepcopy(erp_rows)
    target[1]["NAME"] = None
    result = _result(erp_table, erp_rows, target)
    assert not result.passed
    assert "NAME: nulls 0 -> 1" in result.failures


def test_trailing_space_fails(erp_table: TableDef, erp_rows) -> None:
    target = copy.deepcopy(erp_rows)
    target[0]["NAME"] += " "
    assert "NAME: checksum differs" in _result(erp_table, erp_rows, target).failures


def test_duplicate_replacing_a_row_fails_even_with_equal_count(sales_table, sales_rows) -> None:
    target = [sales_rows[0], sales_rows[0], sales_rows[2]]  # T2 lost, T1 duplicated
    result = _result(sales_table, sales_rows, target)
    assert result.source_rows == result.target_rows
    assert not result.passed
    assert "row checksum differs" in result.failures


def test_values_swapped_between_rows_fail_row_checksum_only(sales_table, sales_rows) -> None:
    target = copy.deepcopy(sales_rows)
    target[0]["AMOUNT"], target[1]["AMOUNT"] = target[1]["AMOUNT"], target[0]["AMOUNT"]
    result = _result(sales_table, sales_rows, target)
    assert result.failures == ["row checksum differs"]  # column profiles are identical


def test_missing_target_column_fails(tmp_path: Path, sales_table, sales_rows) -> None:
    out = ex.extract_table(sales_table, sales_rows, tmp_path)
    with duckdb.connect() as con:
        ld.load_table(con, sales_table, out)
        con.execute('ALTER TABLE "BAYOU_SALES"."MINI_SALES" DROP COLUMN "IS_PROMO"')
        target = rc.profile_duckdb(con, sales_table)
    result = rc.compare(sales_table, rc.profile_source(sales_table, sales_rows), target)
    assert "IS_PROMO: missing in target" in result.failures


def test_loaded_table_reconciles_against_raw_export(tmp_path: Path, erp_table, erp_rows) -> None:
    out = ex.extract_table(erp_table, erp_rows, tmp_path)
    with duckdb.connect() as con:
        ld.load_table(con, erp_table, out)
        result = rc.compare(
            erp_table, rc.profile_source(erp_table, erp_rows), rc.profile_duckdb(con, erp_table)
        )
    assert result.passed, result.failures


def test_deliberately_mismatched_table_fails(tmp_path: Path, sales_table, sales_rows) -> None:
    """A load that lost one file must FAIL and say why."""
    out = ex.extract_table(sales_table, sales_rows, tmp_path)
    with duckdb.connect() as con:
        ld.load_table(con, sales_table, out, file_filter=ld.drop_first_file(out))
        con.execute("""UPDATE "BAYOU_SALES"."MINI_SALES" SET "AMOUNT" = "AMOUNT" + 0.01""")
        result = rc.compare(
            sales_table,
            rc.profile_source(sales_table, sales_rows),
            rc.profile_duckdb(con, sales_table),
        )
    assert not result.passed
    assert "row count 3 -> 1" in result.failures
    assert "AMOUNT: checksum differs" in result.failures
    report = rc.render_report([result])
    assert "**FAIL**" in report and "0 of 1 tables passed" in report


def test_report_lists_pass_and_fail() -> None:
    ok = rc.TableResult("S.A", 1, 1, [], 3)
    bad = rc.TableResult("S.B", 2, 1, ["row count 2 -> 1"], 3, note="lost file")
    report = rc.render_report([ok, bad])
    assert "| `S.A` | 1 | 1 | 3 | PASS |" in report
    assert "### `S.B`" in report and "_lost file_" in report and "- row count 2 -> 1" in report


# --- end to end ------------------------------------------------------------------------


def test_demo_end_to_end(tmp_path: Path) -> None:
    report = tmp_path / "reports" / "reconciliation.md"
    code = main(
        [
            "--samples",
            str(ROOT / "samples"),
            "--data",
            str(ROOT / "samples" / "data"),
            "--extract",
            str(tmp_path / "extract"),
            "--db",
            str(tmp_path / "target.duckdb"),
            "--out",
            str(tmp_path / "snowflake"),
            "--report",
            str(report),
            "demo",
            "--simulate-lost-file",
            "PRODUCT_RETURNS",
        ]
    )
    assert code == 0
    text = report.read_text(encoding="utf-8")
    assert "11 of 12 tables passed" in text
    assert "| `BAYOU_SALES.PRODUCT_RETURNS` |" in text and "**FAIL**" in text
    assert len(list((tmp_path / "snowflake" / "ddl").glob("*.sql"))) == 12
    assert len(list((tmp_path / "snowflake" / "copy").glob("*.sql"))) == 12
