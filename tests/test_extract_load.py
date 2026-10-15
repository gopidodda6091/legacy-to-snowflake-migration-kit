from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import duckdb
import pyarrow as pa
import pytest

from migrate import extract as ex
from migrate import load as ld
from migrate.translate import TableDef


def test_arrow_types_follow_target_schema(erp_table: TableDef) -> None:
    assert ex.arrow_type("NUMBER(12,0)") == pa.int64()
    assert ex.arrow_type("NUMBER(38,0)") == pa.decimal128(38, 0)
    assert ex.arrow_type("NUMBER(14,2)") == pa.decimal128(14, 2)
    assert ex.arrow_type("TIMESTAMP_TZ(3)") == pa.timestamp("us", tz="UTC")
    assert ex.arrow_type("VARIANT") == pa.string()


def test_value_too_precise_for_target_fails_loudly(erp_table: TableDef) -> None:
    rows = [{"CUSTOMER_ID": 1, "BALANCE": "1.005"}]  # NUMBER(14,2) cannot hold 3 decimals
    with pytest.raises(pa.ArrowInvalid):
        ex.to_arrow(erp_table, rows)


def test_hive_extract_is_partitioned(tmp_path: Path, sales_table: TableDef, sales_rows) -> None:
    out = ex.extract_table(sales_table, sales_rows, tmp_path)
    dirs = sorted(p.name for p in out.iterdir())
    assert dirs == ["SALE_DATE=2026-03-01", "SALE_DATE=2026-03-02"]


def test_oracle_extract_is_chunked(tmp_path: Path, erp_table: TableDef, monkeypatch) -> None:
    monkeypatch.setattr(ex, "MAX_ROWS_PER_FILE", 2)
    rows = [{"CUSTOMER_ID": i} for i in range(5)]
    out = ex.extract_table(erp_table, rows, tmp_path)
    assert len(ex.parquet_files(out)) == 3


def _roundtrip(tmp_path: Path, table: TableDef, rows: list[dict]) -> list[tuple]:
    out = ex.extract_table(table, rows, tmp_path / "extract")
    with duckdb.connect(str(tmp_path / "t.duckdb")) as con:
        assert ld.load_table(con, table, out) == len(rows)
        cols = ", ".join(f'"{c.name}"' for c in table.columns)
        sql = f'SELECT {cols} FROM "{table.schema}"."{table.name}" ORDER BY 1'
        return con.execute(sql).fetchall()


def test_hive_roundtrip_restores_partition_column(tmp_path: Path, sales_table, sales_rows) -> None:
    rows = _roundtrip(tmp_path, sales_table, sales_rows)
    assert rows[0][0] == "T1"
    assert rows[0][1] == Decimal("10.50")
    assert rows[0][-1] == "2026-03-01"  # from the directory name, kept as text
    assert rows[2][1] is None


def test_oracle_roundtrip_keeps_unicode_and_empty_string(
    tmp_path: Path, erp_table, erp_rows
) -> None:
    rows = _roundtrip(tmp_path, erp_table, erp_rows)
    assert rows[0][1] == "Amélie Broussard"
    assert rows[1][1] == ""  # not silently turned into NULL
    assert rows[0][5] == bytes.fromhex("00ff" * 8)


def test_file_filter_simulates_lost_file(tmp_path: Path, sales_table, sales_rows) -> None:
    out = ex.extract_table(sales_table, sales_rows, tmp_path / "x")
    with duckdb.connect() as con:
        loaded = ld.load_table(con, sales_table, out, file_filter=ld.drop_first_file(out))
    assert loaded == 1  # the 2026-03-01 file (2 rows) was skipped


def test_duckdb_types() -> None:
    assert ld.duckdb_type("NUMBER(10,0)") == "BIGINT"
    assert ld.duckdb_type("NUMBER(38,0)") == "DECIMAL(38,0)"
    assert ld.duckdb_type("TIMESTAMP_TZ(6)") == "TIMESTAMPTZ"
    assert ld.duckdb_type("VARIANT") == "JSON"


def test_copy_into_template(sales_table: TableDef) -> None:
    sql = ld.render_copy_into(sales_table, database="DB")
    assert sql.count("COPY INTO DB.BAYOU_SALES.MINI_SALES (") == 1
    assert "$1:AMOUNT::NUMBER(10,2)" in sql
    assert "PARSE_JSON($1:TAGS::VARCHAR)" in sql
    assert "SPLIT_PART(SPLIT_PART(METADATA$FILENAME, 'SALE_DATE=', 2), '/', 1)::VARCHAR" in sql
    assert "FROM @DB.MIGRATION.LEGACY_EXTRACT_STAGE/bayou_sales/mini_sales/" in sql
    assert "ON_ERROR = ABORT_STATEMENT" in sql


def test_copy_into_has_no_partition_logic_for_oracle(erp_table: TableDef) -> None:
    sql = ld.render_copy_into(erp_table)
    assert "METADATA$FILENAME" not in sql
    assert "$1:LAST_SEEN::TIMESTAMP_TZ(3)" in sql
