from __future__ import annotations

import pytest

from migrate.typemap import (
    SourceType,
    UnsupportedTypeError,
    category,
    map_hive,
    map_oracle,
    map_type,
    number_spec,
    parse_type,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("NUMBER(10, 2)", SourceType("NUMBER", ("10", "2"))),
        ("VARCHAR2(50 CHAR)", SourceType("VARCHAR2", ("50 CHAR",))),
        ("TIMESTAMP(6) WITH TIME ZONE", SourceType("TIMESTAMP WITH TIME ZONE", ("6",))),
        ("double precision", SourceType("DOUBLE PRECISION")),
        ("ARRAY<STRING>", SourceType("ARRAY")),
        ("STRUCT<a INT, b MAP<STRING, INT>>", SourceType("STRUCT")),
    ],
)
def test_parse_type(text: str, expected: SourceType) -> None:
    assert parse_type(text) == expected


# --- Oracle NUMBER -------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("NUMBER(10)", "NUMBER(10,0)"),
        ("NUMBER(10,2)", "NUMBER(10,2)"),
        ("NUMBER(38,0)", "NUMBER(38,0)"),
        ("NUMBER(*,2)", "NUMBER(38,2)"),
        ("NUMBER(3,5)", "NUMBER(5,5)"),  # scale > precision: 0.00xyz values
        ("NUMBER(10,-2)", "NUMBER(12,0)"),  # negative scale widens integer digits
        ("INTEGER", "NUMBER(38,0)"),
    ],
)
def test_oracle_number_rules(text: str, expected: str) -> None:
    assert map_type("oracle", text).sql == expected


def test_unconstrained_number_is_wide_and_flagged() -> None:
    mapped = map_type("oracle", "NUMBER")
    assert mapped.sql == "NUMBER(38,10)"
    assert any("unconstrained" in w for w in mapped.warnings)


def test_negative_scale_is_flagged() -> None:
    assert map_type("oracle", "NUMBER(10,-2)").warnings


@pytest.mark.parametrize("text", ["NUMBER(39)", "NUMBER(0)", "NUMBER(37,-5)"])
def test_number_outside_snowflake_limits_raises(text: str) -> None:
    with pytest.raises(UnsupportedTypeError):
        map_type("oracle", text)


# --- Oracle dates, strings, LOBs -----------------------------------------------


def test_oracle_date_becomes_timestamp_ntz_with_warning() -> None:
    mapped = map_type("oracle", "DATE")
    assert mapped.sql == "TIMESTAMP_NTZ(0)"
    assert "time of day" in mapped.warnings[0]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("TIMESTAMP", "TIMESTAMP_NTZ(6)"),
        ("TIMESTAMP(3)", "TIMESTAMP_NTZ(3)"),
        ("TIMESTAMP(6) WITH TIME ZONE", "TIMESTAMP_TZ(6)"),
        ("TIMESTAMP WITH LOCAL TIME ZONE", "TIMESTAMP_LTZ(6)"),
        ("TIMESTAMPLTZ", "TIMESTAMP_LTZ(6)"),
    ],
)
def test_oracle_timestamps(text: str, expected: str) -> None:
    assert map_type("oracle", text).sql == expected


@pytest.mark.parametrize(
    "text, expected, warns",
    [
        ("VARCHAR2(50 CHAR)", "VARCHAR(50)", False),
        ("VARCHAR2(50 BYTE)", "VARCHAR(50)", True),
        ("VARCHAR2(50)", "VARCHAR(50)", False),
        ("NVARCHAR2(20)", "VARCHAR(20)", False),
        ("CHAR", "VARCHAR(1)", True),
        ("CHAR(2)", "VARCHAR(2)", True),
        ("CLOB", "VARCHAR(16777216)", True),
        ("NCLOB", "VARCHAR(16777216)", True),
        ("LONG", "VARCHAR(16777216)", True),
    ],
)
def test_oracle_character_types(text: str, expected: str, warns: bool) -> None:
    mapped = map_type("oracle", text)
    assert mapped.sql == expected
    assert bool(mapped.warnings) is warns


@pytest.mark.parametrize(
    "text, expected",
    [
        ("RAW(16)", "BINARY(16)"),
        ("BLOB", "BINARY"),
        ("BINARY_DOUBLE", "FLOAT"),
        ("FLOAT(126)", "FLOAT"),
        ("XMLTYPE", "VARIANT"),
        ("ROWID", "VARCHAR(18)"),
        ("INTERVAL DAY TO SECOND", "VARCHAR(64)"),
    ],
)
def test_oracle_other_types(text: str, expected: str) -> None:
    assert map_type("oracle", text).sql == expected


def test_unknown_oracle_type_raises() -> None:
    with pytest.raises(UnsupportedTypeError):
        map_oracle(SourceType("SDO_GEOMETRY"))


# --- Hive -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("TINYINT", "NUMBER(3,0)"),
        ("SMALLINT", "NUMBER(5,0)"),
        ("INT", "NUMBER(10,0)"),
        ("BIGINT", "NUMBER(19,0)"),
        ("STRING", "VARCHAR"),
        ("VARCHAR(16)", "VARCHAR(16)"),
        ("DECIMAL", "NUMBER(10,0)"),
        ("DECIMAL(12,2)", "NUMBER(12,2)"),
        ("DOUBLE", "FLOAT"),
        ("BOOLEAN", "BOOLEAN"),
        ("DATE", "DATE"),
        ("TIMESTAMP", "TIMESTAMP_NTZ(9)"),
        ("BINARY", "BINARY"),
    ],
)
def test_hive_scalar_types(text: str, expected: str) -> None:
    assert map_type("hive", text).sql == expected


@pytest.mark.parametrize(
    "text", ["ARRAY<STRING>", "MAP<STRING, INT>", "STRUCT<os STRING, is_mobile BOOLEAN>"]
)
def test_hive_complex_types_become_variant(text: str) -> None:
    mapped = map_type("hive", text)
    assert mapped.sql == "VARIANT"
    assert mapped.warnings


def test_hive_decimal_over_38_raises() -> None:
    with pytest.raises(UnsupportedTypeError):
        map_hive(SourceType("DECIMAL", ("40", "2")))


def test_unknown_dialect_raises() -> None:
    with pytest.raises(ValueError):
        map_type("teradata", "INT")


# --- helpers ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target, cat",
    [
        ("NUMBER(10,0)", "int"),
        ("NUMBER(10,2)", "decimal"),
        ("TIMESTAMP_NTZ(0)", "timestamp_ntz"),
        ("TIMESTAMP_LTZ(6)", "timestamp_tz"),
        ("VARCHAR", "string"),
        ("VARIANT", "variant"),
    ],
)
def test_category(target: str, cat: str) -> None:
    assert category(target) == cat


def test_number_spec() -> None:
    assert number_spec("NUMBER(14,2)") == (14, 2)
    assert number_spec("FLOAT") is None
