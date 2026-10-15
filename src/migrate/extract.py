"""Extract legacy rows to Parquet, typed by the *target* (Snowflake) schema.

Typing at extract time means precision problems surface here, loudly, rather
than as silently rounded values after the load. Hive tables are written with
their partition columns as ``COL=value/`` directories; Oracle tables are split
into fixed-size part files.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from migrate.translate import TableDef
from migrate.typemap import category, number_spec

MAX_ROWS_PER_FILE = 250


def arrow_type(target_type: str) -> pa.DataType:
    """Parquet/Arrow type used to carry a Snowflake column."""
    spec = number_spec(target_type)
    if spec is not None:
        precision, scale = spec
        if scale == 0 and precision <= 18:
            return pa.int64()
        return pa.decimal128(precision, scale)
    return {
        "float": pa.float64(),
        "bool": pa.bool_(),
        "date": pa.date32(),
        "timestamp_ntz": pa.timestamp("us"),
        "timestamp_tz": pa.timestamp("us", tz="UTC"),
        "string": pa.string(),
        "binary": pa.binary(),
        "variant": pa.string(),  # JSON text; PARSE_JSON on load
    }[category(target_type)]


def coerce(value: Any, target_type: str) -> Any:
    """Convert one exported legacy value to the Python type Arrow expects."""
    if value is None:
        return None
    cat = category(target_type)
    if cat == "int":
        return int(value)
    if cat == "decimal":
        return Decimal(str(value))
    if cat == "float":
        return float(value)
    if cat == "bool":
        return bool(value)
    if cat == "date":
        return date.fromisoformat(value)
    if cat == "timestamp_ntz":
        return datetime.fromisoformat(value)
    if cat == "timestamp_tz":
        return datetime.fromisoformat(value).astimezone(UTC)
    if cat == "binary":
        return bytes.fromhex(value)
    if cat == "variant":
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def to_arrow(table: TableDef, rows: Iterable[dict[str, Any]]) -> pa.Table:
    """Build a typed Arrow table; raises if a value does not fit its type."""
    rows = list(rows)
    arrays = []
    fields = []
    for col in table.columns:
        typ = arrow_type(col.target_type)
        values = [coerce(r.get(col.name), col.target_type) for r in rows]
        arrays.append(pa.array(values, type=typ))
        fields.append(pa.field(col.name, typ, nullable=col.nullable))
    return pa.Table.from_arrays(arrays, schema=pa.schema(fields))


def extract_table(table: TableDef, rows: Iterable[dict[str, Any]], out_root: Path) -> Path:
    """Write ``table`` to ``out_root/<schema>/<table>/`` and return that directory."""
    target = out_root / table.schema.lower() / table.name.lower()
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    data = to_arrow(table, rows)
    if table.partition_columns:
        pq.write_to_dataset(
            data,
            root_path=str(target),
            partition_cols=table.partition_columns,
            basename_template="part-{i}.parquet",
        )
    else:
        for i, start in enumerate(range(0, max(data.num_rows, 1), MAX_ROWS_PER_FILE)):
            chunk = data.slice(start, MAX_ROWS_PER_FILE)
            pq.write_table(chunk, target / f"part-{i:05d}.parquet")
    return target


def parquet_files(directory: Path) -> list[Path]:
    """All Parquet files under an extract directory, in a stable order."""
    return sorted(directory.rglob("*.parquet"))
