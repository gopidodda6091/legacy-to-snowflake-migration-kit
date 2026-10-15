from __future__ import annotations

from pathlib import Path

import pytest
import sqlglot

from migrate.translate import (
    ManualRewriteRequiredError,
    load_tables,
    needs_manual_rewrite,
    parse_table,
    render_snowflake_ddl,
    translate_query,
)

SAMPLES = Path(__file__).resolve().parents[1] / "samples"

ORACLE_DDL = """
-- comment with (parentheses) before the statement
CREATE TABLE BAYOU_ERP.T (
    ID        NUMBER(10)    NOT NULL,
    AMOUNT    NUMBER(12,2),
    ROUNDED   NUMBER(10,-2),
    QTY       INTEGER,
    NOTES     LONG,
    WAIT_TIME INTERVAL DAY(2) TO SECOND(6),
    CREATED   DATE DEFAULT SYSDATE,
    CONSTRAINT PK_T PRIMARY KEY (ID)
)
TABLESPACE USERS
PCTFREE 10
STORAGE (INITIAL 64K);
"""

HIVE_DDL = """
CREATE EXTERNAL TABLE IF NOT EXISTS bayou_web.events (
    event_id STRING COMMENT 'it\\'s unique',
    tags     ARRAY<STRING>,
    device   STRUCT<os:STRING, is_mobile:BOOLEAN>
)
PARTITIONED BY (event_date STRING, region STRING)
STORED AS ORC
LOCATION 'hdfs:///x'
TBLPROPERTIES ('orc.compress'='SNAPPY')
"""


def test_oracle_ddl_parses_despite_storage_clauses() -> None:
    t = parse_table(ORACLE_DDL, "oracle")
    assert (t.schema, t.name) == ("BAYOU_ERP", "T")
    assert [c.name for c in t.columns] == [
        "ID",
        "AMOUNT",
        "ROUNDED",
        "QTY",
        "NOTES",
        "WAIT_TIME",
        "CREATED",
    ]
    assert t.primary_key == ["ID"]


def test_oracle_types_go_through_typemap() -> None:
    t = parse_table(ORACLE_DDL, "oracle")
    types = {c.name: c.target_type for c in t.columns}
    assert types == {
        "ID": "NUMBER(10,0)",
        "AMOUNT": "NUMBER(12,2)",
        "ROUNDED": "NUMBER(12,0)",  # negative scale survived parsing
        "QTY": "NUMBER(38,0)",  # INTEGER, not sqlglot's bare NUMBER
        "NOTES": "VARCHAR(16777216)",  # LONG is text, not BIGINT
        "WAIT_TIME": "VARCHAR(64)",
        "CREATED": "TIMESTAMP_NTZ(0)",
    }


def test_oracle_default_and_not_null() -> None:
    t = parse_table(ORACLE_DDL, "oracle")
    assert t.column("CREATED").default_sql == "CURRENT_TIMESTAMP()"
    assert not t.column("ID").nullable
    assert t.column("AMOUNT").nullable


def test_hive_partition_columns_are_appended_and_clustered() -> None:
    t = parse_table(HIVE_DDL, "hive")
    assert [c.name for c in t.columns][-2:] == ["EVENT_DATE", "REGION"]
    assert t.partition_columns == ["EVENT_DATE", "REGION"]
    ddl = render_snowflake_ddl(t)
    assert "CLUSTER BY (EVENT_DATE, REGION)" in ddl


def test_hive_complex_types_and_comments() -> None:
    t = parse_table(HIVE_DDL, "hive")
    assert t.column("TAGS").target_type == "VARIANT"
    assert t.column("DEVICE").target_type == "VARIANT"
    assert t.column("EVENT_ID").comment == "it's unique"
    assert "COMMENT 'it''s unique'" in render_snowflake_ddl(t)


def test_rendered_ddl_is_valid_snowflake_sql() -> None:
    for ddl, dialect in ((ORACLE_DDL, "oracle"), (HIVE_DDL, "hive")):
        rendered = render_snowflake_ddl(parse_table(ddl, dialect), database="DB")
        parsed = sqlglot.parse(rendered, read="snowflake")
        assert len([p for p in parsed if p is not None]) == 1


def test_review_warnings_are_emitted_as_comments() -> None:
    rendered = render_snowflake_ddl(parse_table(ORACLE_DDL, "oracle"))
    assert "-- REVIEW T.CREATED: Oracle DATE carries a time of day" in rendered


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE OR REPLACE PROCEDURE p AS BEGIN NULL; END;",
        "create or replace package body pkg as end;",
        "CREATE TRIGGER trg BEFORE INSERT ON t FOR EACH ROW BEGIN NULL; END;",
        "CREATE TEMPORARY FUNCTION clean_sku AS 'com.bayou.udf.CleanSku'",
    ],
)
def test_procedural_code_is_flagged(sql: str) -> None:
    assert needs_manual_rewrite(sql)
    with pytest.raises(ManualRewriteRequiredError):
        parse_table(sql, "oracle")


def test_plain_table_is_not_flagged() -> None:
    assert not needs_manual_rewrite(ORACLE_DDL)


def test_translate_query_oracle_functions() -> None:
    out = translate_query("SELECT NVL(a, 0), DECODE(b, 1, 'x', 'y') FROM t", "oracle")
    assert "COALESCE(a, 0)" in out
    assert "CASE WHEN b = 1 THEN 'x' ELSE 'y' END" in out


def test_translate_query_hive_functions() -> None:
    out = translate_query("SELECT size(tags) FROM events", "hive")
    assert "ARRAY_SIZE(tags)" in out


def test_unsupported_dialect() -> None:
    with pytest.raises(ValueError):
        parse_table("CREATE TABLE t (a INT)", "db2")


def test_all_sample_tables_translate() -> None:
    oracle = load_tables(SAMPLES / "oracle_ddl", "oracle")
    hive = load_tables(SAMPLES / "hive_ddl", "hive")
    assert len(oracle.tables) + len(hive.tables) == 12
    assert [p.name for p in oracle.flagged] == ["refresh_store_kpis.sql"]
    assert hive.flagged == []
    for table in oracle.tables + hive.tables:
        assert sqlglot.parse_one(render_snowflake_ddl(table), read="snowflake") is not None
