"""Translate legacy Hive / Oracle DDL (and queries) to Snowflake.

sqlglot does the parsing: table names, column definitions, constraints,
defaults, Hive partitioning. Column *types* go through :mod:`migrate.typemap`
so precision and time-zone decisions are explicit. Procedural code is never
translated; it is detected and flagged for manual rewrite.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp

from migrate.typemap import map_type

DIALECTS = ("oracle", "hive")

_MANUAL_REWRITE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMPORARY\s+)?"
    r"(?:PROCEDURE|FUNCTION|PACKAGE(?:\s+BODY)?|TRIGGER|MACRO)\b",
    re.IGNORECASE,
)
# Oracle syntax sqlglot cannot parse is rewritten to placeholder type names
# before parsing and restored afterwards.
_NEG_SCALE = re.compile(r"\bNUMBER\s*\(\s*(\d+)\s*,\s*-(\d+)\s*\)", re.IGNORECASE)
_INTERVAL = re.compile(
    r"\bINTERVAL\s+(YEAR|DAY)\s*(?:\(\s*\d+\s*\))?\s+TO\s+(MONTH|SECOND)\s*(?:\(\s*\d+\s*\))?",
    re.IGNORECASE,
)
# Oracle pseudo-columns used as defaults that sqlglot leaves untouched.
_ORACLE_DEFAULTS = {
    "SYSDATE": "CURRENT_TIMESTAMP()",
    "SYSTIMESTAMP": "CURRENT_TIMESTAMP()",
    "USER": "CURRENT_USER()",
}
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_PLACEHOLDER = re.compile(r"^(NUMBER_NEGSCALE_(\d+)_(\d+)|INTERVAL_(YEAR|DAY)_TO_(MONTH|SECOND))$")


@dataclass(frozen=True)
class Column:
    name: str
    source_type: str
    target_type: str
    nullable: bool = True
    default_sql: str | None = None
    comment: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass
class TableDef:
    dialect: str
    schema: str
    name: str
    columns: list[Column]
    primary_key: list[str] = field(default_factory=list)
    partition_columns: list[str] = field(default_factory=list)

    @property
    def fqn(self) -> str:
        return f"{self.schema}.{self.name}"

    def column(self, name: str) -> Column:
        for col in self.columns:
            if col.name == name:
                return col
        raise KeyError(name)

    @property
    def warnings(self) -> list[str]:
        return [f"{self.name}.{c.name}: {w}" for c in self.columns for w in c.warnings]


class ManualRewriteRequiredError(ValueError):
    """Raised for procedural code (PL/SQL, UDFs) that is out of scope."""


def needs_manual_rewrite(sql: str) -> bool:
    """True for stored procedures, functions, packages, triggers and Hive UDFs."""
    return bool(_MANUAL_REWRITE.search(sql))


def _close_paren_index(sql: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced parentheses in DDL")


def strip_comments(sql: str) -> str:
    """Remove ``--`` and ``/* */`` comments (DDL string literals must not contain ``--``)."""
    return _COMMENTS.sub("", sql)


def prepare_oracle(ddl: str) -> str:
    """Drop physical storage clauses and protect types sqlglot cannot parse."""
    body = strip_comments(ddl).strip().rstrip(";").strip()
    start = body.index("(")
    body = body[: _close_paren_index(body, start) + 1]  # TABLESPACE, PCTFREE, ...
    body = _NEG_SCALE.sub(lambda m: f"NUMBER_NEGSCALE_{m[1]}_{m[2]}", body)
    return _INTERVAL.sub(lambda m: f"INTERVAL_{m[1].upper()}_TO_{m[2].upper()}", body)


def _source_type_text(kind: exp.DataType, dialect: str) -> str:
    """Type text as written in the legacy dialect, undoing sqlglot normalisation."""
    text = kind.sql(dialect=dialect)
    if dialect == "oracle":
        placeholder = _PLACEHOLDER.match(text.upper())
        if placeholder and placeholder[2]:
            return f"NUMBER({placeholder[2]},-{placeholder[3]})"
        if placeholder:
            return f"INTERVAL {placeholder[4]} TO {placeholder[5]}"
        if kind.this in (exp.DataType.Type.INT, exp.DataType.Type.SMALLINT):
            return "INTEGER"  # sqlglot renders Oracle INTEGER as NUMBER
        if kind.this == exp.DataType.Type.BIGINT:
            return "LONG"  # Oracle LONG is a character type, not an integer
    return text


def _column(defn: exp.ColumnDef, dialect: str) -> Column:
    kind = defn.args.get("kind")
    if kind is None:
        raise ValueError(f"column {defn.name} has no type")
    source = _source_type_text(kind, dialect)
    mapped = map_type(dialect, source)
    nullable, default_sql, comment = True, None, None
    for constraint in defn.constraints:
        ckind = constraint.args.get("kind")
        if isinstance(ckind, exp.NotNullColumnConstraint) and not ckind.args.get("allow_null"):
            nullable = False
        elif isinstance(ckind, exp.DefaultColumnConstraint):
            default_sql = ckind.this.sql(dialect="snowflake")
            default_sql = _ORACLE_DEFAULTS.get(default_sql.upper(), default_sql)
        elif isinstance(ckind, exp.CommentColumnConstraint):
            comment = ckind.this.name
    return Column(
        name=defn.name.upper(),
        source_type=source,
        target_type=mapped.sql,
        nullable=nullable,
        default_sql=default_sql,
        comment=comment,
        warnings=mapped.warnings,
    )


def _primary_key(schema: exp.Schema) -> list[str]:
    for node in schema.expressions:
        pk = node.find(exp.PrimaryKey)
        if pk is not None and not isinstance(node, exp.ColumnDef):
            return [e.name.upper() for e in pk.expressions]
    inline = exp.PrimaryKeyColumnConstraint
    return [
        node.name.upper()
        for node in schema.expressions
        if isinstance(node, exp.ColumnDef)
        and any(isinstance(c.args.get("kind"), inline) for c in node.constraints)
    ]


def parse_table(ddl: str, dialect: str) -> TableDef:
    """Parse one ``CREATE TABLE`` statement into a :class:`TableDef`."""
    if dialect not in DIALECTS:
        raise ValueError(f"unsupported dialect {dialect!r}")
    if needs_manual_rewrite(ddl):
        raise ManualRewriteRequiredError("procedural object: rewrite manually")
    text = prepare_oracle(ddl) if dialect == "oracle" else strip_comments(ddl).strip().rstrip(";")
    tree = sqlglot.parse_one(text, read=dialect)
    if not isinstance(tree, exp.Create) or not isinstance(tree.this, exp.Schema):
        raise ValueError("expected a CREATE TABLE statement with a column list")
    table = tree.this.this
    columns = [_column(c, dialect) for c in tree.this.expressions if isinstance(c, exp.ColumnDef)]
    partitions: list[str] = []
    props = tree.args.get("properties")
    if props is not None:
        part = props.find(exp.PartitionedByProperty)
        if part is not None:
            part_defs = [c for c in part.this.expressions if isinstance(c, exp.ColumnDef)]
            columns += [_column(c, dialect) for c in part_defs]
            partitions = [c.name.upper() for c in part_defs]
    return TableDef(
        dialect=dialect,
        schema=(table.db or "PUBLIC").upper(),
        name=table.name.upper(),
        columns=columns,
        primary_key=_primary_key(tree.this),
        partition_columns=partitions,
    )


def render_snowflake_ddl(table: TableDef, database: str = "BAYOU_RETAIL") -> str:
    """Render a Snowflake ``CREATE TABLE`` for ``table``."""
    lines = []
    for col in table.columns:
        line = f"    {col.name} {col.target_type}"
        if col.default_sql:
            line += f" DEFAULT {col.default_sql}"
        if not col.nullable:
            line += " NOT NULL"
        if col.comment:
            line += " COMMENT '" + col.comment.replace("'", "''") + "'"
        lines.append(line)
    if table.primary_key:
        lines.append(f"    CONSTRAINT PK_{table.name} PRIMARY KEY ({', '.join(table.primary_key)})")
    ddl = f"CREATE TABLE IF NOT EXISTS {database}.{table.fqn} (\n" + ",\n".join(lines) + "\n)"
    if table.partition_columns:
        ddl += f"\nCLUSTER BY ({', '.join(table.partition_columns)})"
    ddl += f"\nCOMMENT = 'Migrated from {table.dialect}: {table.fqn}';\n"
    warnings = table.warnings
    if warnings:
        ddl = "".join(f"-- REVIEW {w}\n" for w in warnings) + ddl
    return ddl


def translate_query(sql: str, dialect: str) -> str:
    """Transpile a legacy SELECT / view body to Snowflake SQL with sqlglot."""
    if needs_manual_rewrite(sql):
        raise ManualRewriteRequiredError("procedural object: rewrite manually")
    return ";\n".join(sqlglot.transpile(sql, read=dialect, write="snowflake", pretty=True))


@dataclass
class TranslationResult:
    tables: list[TableDef]
    flagged: list[Path]


def load_tables(ddl_dir: Path, dialect: str) -> TranslationResult:
    """Parse every ``*.sql`` file in ``ddl_dir``; flag procedural files."""
    tables, flagged = [], []
    for path in sorted(ddl_dir.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        if needs_manual_rewrite(sql):
            flagged.append(path)
            continue
        tables.append(parse_table(sql, dialect))
    return TranslationResult(tables, flagged)


def write_snowflake_ddl(tables: list[TableDef], out_dir: Path, database: str) -> list[Path]:
    """Write one Snowflake DDL file per table; returns the paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for table in tables:
        path = out_dir / f"{table.schema.lower()}__{table.name.lower()}.sql"
        path.write_text(render_snowflake_ddl(table, database), encoding="utf-8")
        paths.append(path)
    return paths
