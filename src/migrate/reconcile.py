"""Prove a migrated table matches its source.

Each side is reduced to a :class:`TableProfile`:

* row count
* an order-independent row checksum (sum of per-row hashes mod 2**64), which
  catches duplicated / missing / re-associated rows even when counts match
* per column: checksum, null count, distinct count

Values are normalised before hashing so representation differences that are
*not* data differences (``1.50`` vs ``1.5``, a UTC timestamp with or without
offset, JSON key order) do not fail, while real differences (a trailing space,
NULL vs empty string, a lost row) do.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from migrate.translate import TableDef
from migrate.typemap import category

MOD = 2**64
NULL_TOKEN = "\x00NULL"


def normalize(value: Any, target_type: str) -> str | None:
    """Canonical text for a value of a Snowflake type; None stays None."""
    if value is None:
        return None
    cat = category(target_type)
    if cat in {"int", "decimal"}:
        d = Decimal(str(value)).normalize()
        return format(d, "f") if d != 0 else "0"
    if cat == "float":
        return f"{float(value):.12g}"
    if cat == "bool":
        return "true" if value in (True, "true", "TRUE", 1) else "false"
    if cat == "date":
        day = value if isinstance(value, date) else date.fromisoformat(value)
        return day.isoformat()
    if cat == "timestamp_ntz":
        ts = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return ts.replace(tzinfo=None).isoformat(timespec="microseconds")
    if cat == "timestamp_tz":
        ts = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if ts.tzinfo is None:
            raise ValueError("timestamp_tz value without offset")
        return ts.astimezone(UTC).isoformat(timespec="microseconds")
    if cat == "binary":
        if isinstance(value, bytes | bytearray | memoryview):
            return bytes(value).hex()
        return str(value).lower()
    if cat == "variant":
        parsed = json.loads(value) if isinstance(value, str) else value
        return json.dumps(parsed, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _hash(text: str) -> int:
    return int.from_bytes(hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest(), "big")


@dataclass
class ColumnProfile:
    checksum: int = 0
    nulls: int = 0
    distinct: set[str] = field(default_factory=set)

    @property
    def distinct_count(self) -> int:
        return len(self.distinct)


@dataclass
class TableProfile:
    rows: int
    row_checksum: int
    columns: dict[str, ColumnProfile]


def profile(
    table: TableDef, rows: Iterable[Sequence[Any]], column_names: Sequence[str]
) -> TableProfile:
    """Profile ``rows`` whose values are ordered as ``column_names``."""
    types = {c.name: c.target_type for c in table.columns}
    cols = {name: ColumnProfile() for name in column_names}
    count, row_sum = 0, 0
    for row in rows:
        count += 1
        parts = []
        for name, raw in zip(column_names, row, strict=True):
            norm = normalize(raw, types[name])
            prof = cols[name]
            if norm is None:
                prof.nulls += 1
                parts.append(NULL_TOKEN)
                continue
            prof.checksum = (prof.checksum + _hash(norm)) % MOD
            prof.distinct.add(norm)
            parts.append(norm)
        row_sum = (row_sum + _hash("\x1f".join(parts))) % MOD
    return TableProfile(count, row_sum, cols)


def profile_source(table: TableDef, records: Iterable[dict[str, Any]]) -> TableProfile:
    """Profile exported legacy records (dicts keyed by column name)."""
    names = [c.name for c in table.columns]
    return profile(table, ([r.get(n) for n in names] for r in records), names)


def profile_duckdb(con: duckdb.DuckDBPyConnection, table: TableDef) -> TableProfile:
    """Profile the loaded target table; missing columns are reported, not hidden."""
    existing = {
        r[0]
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ?",
            [table.schema, table.name],
        ).fetchall()
    }
    names = [c.name for c in table.columns if c.name in existing]
    select = ", ".join(f'"{n}"' for n in names) or "NULL"
    rows = con.execute(f'SELECT {select} FROM "{table.schema}"."{table.name}"').fetchall()
    return profile(table, rows if names else [() for _ in rows], names)


# --------------------------------------------------------------------------
# Comparison and report
# --------------------------------------------------------------------------


@dataclass
class TableResult:
    table: str
    source_rows: int
    target_rows: int
    failures: list[str]
    columns_checked: int
    note: str = ""

    @property
    def passed(self) -> bool:
        return not self.failures


def compare(table: TableDef, source: TableProfile, target: TableProfile) -> TableResult:
    """Compare two profiles; every difference becomes a human-readable failure."""
    failures = []
    if source.rows != target.rows:
        failures.append(f"row count {source.rows} -> {target.rows}")
    if source.row_checksum != target.row_checksum:
        failures.append("row checksum differs")
    for name, src in source.columns.items():
        tgt = target.columns.get(name)
        if tgt is None:
            failures.append(f"{name}: missing in target")
            continue
        if src.checksum != tgt.checksum:
            failures.append(f"{name}: checksum differs")
        if src.nulls != tgt.nulls:
            failures.append(f"{name}: nulls {src.nulls} -> {tgt.nulls}")
        if src.distinct_count != tgt.distinct_count:
            failures.append(f"{name}: distinct {src.distinct_count} -> {tgt.distinct_count}")
    return TableResult(table.fqn, source.rows, target.rows, failures, len(source.columns))


def render_report(results: list[TableResult], title: str = "Reconciliation report") -> str:
    """Markdown report: summary table, then detail for every failing table."""
    passed = sum(r.passed for r in results)
    lines = [
        f"# {title}",
        "",
        f"**{passed} of {len(results)} tables passed.** "
        "Checks per table: row count, row checksum, and per-column checksum, "
        "null count and distinct count.",
        "",
        "| Table | Source rows | Target rows | Columns | Result |",
        "|---|---:|---:|---:|---|",
    ]
    for r in results:
        status = "PASS" if r.passed else "**FAIL**"
        lines.append(
            f"| `{r.table}` | {r.source_rows} | {r.target_rows} | {r.columns_checked} | {status} |"
        )
    failed = [r for r in results if not r.passed]
    if failed:
        lines += ["", "## Failures", ""]
        for r in failed:
            lines.append(f"### `{r.table}`")
            if r.note:
                lines += ["", f"_{r.note}_"]
            lines.append("")
            lines += [f"- {f}" for f in r.failures]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_report(results: list[TableResult], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(results), encoding="utf-8")
    return path
