"""Load Parquet extracts: DuckDB locally, rendered COPY INTO for Snowflake.

Both paths load by column name from the same Parquet files. Locally DuckDB
stands in for Snowflake so the whole flow (and reconciliation) runs offline;
for Snowflake the kit renders ``COPY INTO`` statements from Jinja templates.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import duckdb
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from migrate.extract import parquet_files
from migrate.translate import Column, TableDef
from migrate.typemap import category, number_spec

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / "snowflake"


def duckdb_type(target_type: str) -> str:
    """DuckDB equivalent of a Snowflake column type."""
    spec = number_spec(target_type)
    if spec is not None:
        precision, scale = spec
        return "BIGINT" if scale == 0 and precision <= 18 else f"DECIMAL({precision},{scale})"
    return {
        "float": "DOUBLE",
        "bool": "BOOLEAN",
        "date": "DATE",
        "timestamp_ntz": "TIMESTAMP",
        "timestamp_tz": "TIMESTAMPTZ",
        "string": "VARCHAR",
        "binary": "BLOB",
        "variant": "JSON",
    }[category(target_type)]


def create_table_sql(table: TableDef) -> str:
    cols = ",\n  ".join(
        f'"{c.name}" {duckdb_type(c.target_type)}' + ("" if c.nullable else " NOT NULL")
        for c in table.columns
    )
    return f'CREATE OR REPLACE TABLE "{table.schema}"."{table.name}" (\n  {cols}\n)'


def _select_expr(col: Column) -> str:
    return f'CAST("{col.name}" AS {duckdb_type(col.target_type)}) AS "{col.name}"'


def load_table(
    con: duckdb.DuckDBPyConnection,
    table: TableDef,
    extract_dir: Path,
    file_filter: Callable[[Path], bool] | None = None,
) -> int:
    """(Re)create ``table`` in DuckDB and load its Parquet files; returns rows loaded.

    ``file_filter`` decides which files are loaded. The demo uses it to
    simulate a COPY that silently skipped a file.
    """
    files = [p for p in parquet_files(extract_dir) if file_filter is None or file_filter(p)]
    con.execute(f'CREATE SCHEMA IF NOT EXISTS "{table.schema}"')
    con.execute(create_table_sql(table))
    if not files:
        return 0
    file_list = ", ".join(f"'{p.as_posix()}'" for p in files)
    select = ", ".join(_select_expr(c) for c in table.columns)
    hive = "true" if table.partition_columns else "false"
    con.execute(
        f'INSERT INTO "{table.schema}"."{table.name}" SELECT {select} '
        f"FROM read_parquet([{file_list}], hive_partitioning = {hive}, "
        f"hive_types_autocast = false, union_by_name = true)"
    )
    row = con.execute(f'SELECT count(*) FROM "{table.schema}"."{table.name}"').fetchone()
    return int(row[0]) if row else 0


def drop_first_file(extract_dir: Path) -> Callable[[Path], bool]:
    """File filter that skips the first Parquet file (simulated lost file)."""
    files = parquet_files(extract_dir)
    lost = files[0] if files else None
    return lambda p: p != lost


# --------------------------------------------------------------------------
# Snowflake COPY INTO rendering
# --------------------------------------------------------------------------


def _environment(template_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def copy_columns(table: TableDef) -> list[dict[str, str]]:
    """Per-column source expressions for a transforming COPY from Parquet."""
    out = []
    for col in table.columns:
        if col.name in table.partition_columns:
            expr = (
                f"SPLIT_PART(SPLIT_PART(METADATA$FILENAME, '{col.name}=', 2), '/', 1)"
                f"::{col.target_type}"
            )
        elif category(col.target_type) == "variant":
            expr = f"PARSE_JSON($1:{col.name}::VARCHAR)"
        else:
            expr = f"$1:{col.name}::{col.target_type}"
        out.append({"name": col.name, "expr": expr})
    return out


def render_copy_into(
    table: TableDef,
    database: str = "BAYOU_RETAIL",
    stage: str = "MIGRATION.LEGACY_EXTRACT_STAGE",
    file_format: str = "MIGRATION.FF_PARQUET",
    template_dir: Path = TEMPLATE_DIR,
) -> str:
    """Render the COPY INTO statement for one table."""
    template = _environment(template_dir).get_template("copy_into.sql.j2")
    return template.render(
        database=database,
        table=table,
        stage=stage,
        file_format=file_format,
        stage_path=f"{table.schema.lower()}/{table.name.lower()}/",
        columns=copy_columns(table),
    )


def write_copy_scripts(tables: list[TableDef], out_dir: Path, database: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for table in tables:
        path = out_dir / f"{table.schema.lower()}__{table.name.lower()}.copy.sql"
        path.write_text(render_copy_into(table, database), encoding="utf-8")
        paths.append(path)
    return paths
