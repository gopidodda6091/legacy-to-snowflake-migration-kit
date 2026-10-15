"""Legacy (Oracle, Hive) to Snowflake column type rules.

The rules are deliberately explicit rather than delegated to a transpiler:
type mapping is where silent data loss happens (precision, time zones, byte
vs character lengths), so every decision lives here and is unit-tested.

Input is a :class:`SourceType` (base name + raw parameters, as written in the
legacy DDL). Output is a :class:`MappedType` with the Snowflake type and any
warnings a reviewer should read before cutover.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SNOWFLAKE_MAX_PRECISION = 38
VARCHAR_MAX = 16_777_216


@dataclass(frozen=True)
class SourceType:
    """A legacy column type: ``NUMBER(10,2)`` -> ``SourceType("NUMBER", ("10", "2"))``."""

    name: str
    params: tuple[str, ...] = ()


@dataclass(frozen=True)
class MappedType:
    """Snowflake type plus reviewer warnings."""

    sql: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


class UnsupportedTypeError(ValueError):
    """Raised for types with no safe automatic mapping."""


_TYPE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_. ]*)(?:\((.*)\))?\s*([A-Za-z ]*)$")


def parse_type(text: str) -> SourceType:
    """Parse ``'VARCHAR2(50 CHAR)'`` into ``SourceType('VARCHAR2', ('50 CHAR',))``.

    Trailing words after the parameter list (``TIMESTAMP(6) WITH TIME ZONE``)
    are folded into the name so the mapper sees ``TIMESTAMP WITH TIME ZONE``.
    Complex Hive types keep only their base name (``ARRAY<...>`` -> ``ARRAY``).
    """
    raw = text.strip()
    if "<" in raw:
        return SourceType(raw.split("<", 1)[0].strip().upper())
    match = _TYPE_RE.match(raw)
    if not match:
        raise UnsupportedTypeError(f"cannot parse type {text!r}")
    base, params, suffix = match.groups()
    name = " ".join(f"{base} {suffix}".upper().split())
    args = tuple(p.strip() for p in params.split(",")) if params else ()
    return SourceType(name, args)


# --------------------------------------------------------------------------
# Oracle
# --------------------------------------------------------------------------


def _oracle_number(params: tuple[str, ...]) -> MappedType:
    """Oracle NUMBER rules.

    * ``NUMBER``          -> ``NUMBER(38,10)`` + warning (Oracle stores any scale)
    * ``NUMBER(p)``       -> ``NUMBER(p,0)``
    * ``NUMBER(p,s)``     -> ``NUMBER(p,s)``
    * ``NUMBER(*,s)``     -> ``NUMBER(38,s)``
    * ``NUMBER(p,-s)``    -> ``NUMBER(p+s,0)``: values are rounded to 10^s and
      can hold p+s integer digits
    * ``NUMBER(p,s)`` with ``s > p`` -> ``NUMBER(s,s)``: pure fractions such as
      ``0.00123`` need ``s`` digits in total
    """
    if not params:
        return MappedType(
            f"NUMBER({SNOWFLAKE_MAX_PRECISION},10)",
            ("unconstrained NUMBER: profile max scale before cutover",),
        )
    precision_raw = params[0]
    scale = int(params[1]) if len(params) > 1 else 0
    precision = SNOWFLAKE_MAX_PRECISION if precision_raw == "*" else int(precision_raw)
    if not 1 <= precision <= SNOWFLAKE_MAX_PRECISION:
        raise UnsupportedTypeError(f"NUMBER precision {precision} outside 1..38")
    if scale < 0:
        widened = precision - scale
        if widened > SNOWFLAKE_MAX_PRECISION:
            raise UnsupportedTypeError(f"NUMBER({precision},{scale}) needs {widened} digits")
        return MappedType(
            f"NUMBER({widened},0)",
            (f"negative scale {scale}: Oracle rounded on write, Snowflake will not",),
        )
    if scale > precision:
        return MappedType(f"NUMBER({scale},{scale})")
    return MappedType(f"NUMBER({precision},{scale})")


def _char_length(params: tuple[str, ...], default: int) -> tuple[int, tuple[str, ...]]:
    """Length and warnings for ``VARCHAR2(n [BYTE|CHAR])``.

    Snowflake lengths are in characters. A BYTE-semantics column never holds
    more characters than bytes, so ``n`` is always safe; it may just be loose.
    """
    if not params:
        return default, ()
    parts = params[0].split()
    length = int(parts[0])
    if len(parts) > 1 and parts[1].upper() == "BYTE":
        return length, ("BYTE length semantics: Snowflake length is in characters",)
    return length, ()


def map_oracle(source: SourceType) -> MappedType:
    """Map an Oracle column type to Snowflake."""
    name, params = source.name, source.params
    if name in {"NUMBER", "NUMERIC", "DECIMAL", "DEC"}:
        return _oracle_number(params)
    if name in {"INTEGER", "INT", "SMALLINT"}:
        return MappedType("NUMBER(38,0)")
    if name in {"FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE", "DOUBLE PRECISION", "REAL"}:
        return MappedType("FLOAT")
    if name == "DATE":
        return MappedType(
            "TIMESTAMP_NTZ(0)", ("Oracle DATE carries a time of day; mapped to TIMESTAMP_NTZ",)
        )
    if name in {"TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIMESTAMP WITH LOCAL TIME ZONE"}:
        scale = int(params[0]) if params else 6
        suffix = {"TIMESTAMP": "NTZ", "TIMESTAMP WITH TIME ZONE": "TZ"}.get(name, "LTZ")
        return MappedType(f"TIMESTAMP_{suffix}({scale})")
    if name == "TIMESTAMPLTZ":  # how sqlglot renders WITH LOCAL TIME ZONE
        return MappedType("TIMESTAMP_LTZ(6)")
    if name in {"VARCHAR2", "VARCHAR", "NVARCHAR2", "CHAR", "NCHAR"}:
        fixed = name in {"CHAR", "NCHAR"}
        length, warnings = _char_length(params, 1 if fixed else 4000)
        if fixed:
            warnings += ("CHAR is not blank-padded in Snowflake; trailing spaces are not added",)
        return MappedType(f"VARCHAR({length})", warnings)
    if name in {"CLOB", "NCLOB", "LONG"}:
        return MappedType(f"VARCHAR({VARCHAR_MAX})", ("values above 16 MB will fail to load",))
    if name in {"BLOB", "RAW", "LONG RAW", "BFILE"}:
        size = f"({int(params[0])})" if name == "RAW" and params else ""
        return MappedType(f"BINARY{size}")
    if name in {"ROWID", "UROWID"}:
        return MappedType("VARCHAR(18)", ("ROWID values are meaningless after migration",))
    if name in {"XMLTYPE", "SYS.XMLTYPE"}:
        return MappedType("VARIANT", ("XMLTYPE: load with PARSE_XML",))
    if name.startswith("INTERVAL"):
        return MappedType("VARCHAR(64)", (f"{name}: no Snowflake interval type, stored as text",))
    raise UnsupportedTypeError(f"no Oracle mapping for {name}")


# --------------------------------------------------------------------------
# Hive
# --------------------------------------------------------------------------

_HIVE_INTEGERS = {"TINYINT": 3, "SMALLINT": 5, "INT": 10, "INTEGER": 10, "BIGINT": 19}
_HIVE_COMPLEX = {"ARRAY", "MAP", "STRUCT", "UNIONTYPE"}


def map_hive(source: SourceType) -> MappedType:
    """Map a Hive column type to Snowflake."""
    name, params = source.name, source.params
    if name in _HIVE_INTEGERS:
        return MappedType(f"NUMBER({_HIVE_INTEGERS[name]},0)")
    if name in {"STRING", "TEXT"}:
        return MappedType("VARCHAR")
    if name in {"VARCHAR", "CHAR"}:
        length = int(params[0]) if params else VARCHAR_MAX
        return MappedType(f"VARCHAR({length})")
    if name in {"FLOAT", "DOUBLE", "DOUBLE PRECISION"}:
        return MappedType("FLOAT")
    if name in {"DECIMAL", "NUMERIC"}:
        precision = int(params[0]) if params else 10  # Hive default DECIMAL(10,0)
        scale = int(params[1]) if len(params) > 1 else 0
        if precision > SNOWFLAKE_MAX_PRECISION:
            raise UnsupportedTypeError(f"DECIMAL precision {precision} > 38")
        return MappedType(f"NUMBER({precision},{scale})")
    if name == "BOOLEAN":
        return MappedType("BOOLEAN")
    if name == "DATE":
        return MappedType("DATE")
    if name == "TIMESTAMP":
        return MappedType("TIMESTAMP_NTZ(9)")
    if name == "BINARY":
        return MappedType("BINARY")
    if name in _HIVE_COMPLEX:
        return MappedType("VARIANT", (f"{name} loaded as VARIANT; query with : and FLATTEN",))
    raise UnsupportedTypeError(f"no Hive mapping for {name}")


def map_type(dialect: str, type_text: str) -> MappedType:
    """Entry point: map raw type text for ``dialect`` ('oracle' or 'hive')."""
    source = parse_type(type_text)
    if dialect == "oracle":
        return map_oracle(source)
    if dialect == "hive":
        return map_hive(source)
    raise ValueError(f"unsupported dialect {dialect!r}")


# --------------------------------------------------------------------------
# Target-type helpers shared by extract, load and reconcile
# --------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"^NUMBER\((\d+),(\d+)\)$")


def number_spec(target_type: str) -> tuple[int, int] | None:
    """``'NUMBER(10,2)'`` -> ``(10, 2)``; None for non-NUMBER types."""
    match = _NUMBER_RE.match(target_type)
    return (int(match[1]), int(match[2])) if match else None


def category(target_type: str) -> str:
    """Coarse value category of a Snowflake type, used for coercion and hashing."""
    spec = number_spec(target_type)
    if spec is not None:
        return "int" if spec[1] == 0 else "decimal"
    base = target_type.split("(", 1)[0]
    categories = {
        "FLOAT": "float",
        "BOOLEAN": "bool",
        "DATE": "date",
        "TIMESTAMP_NTZ": "timestamp_ntz",
        "TIMESTAMP_TZ": "timestamp_tz",
        "TIMESTAMP_LTZ": "timestamp_tz",
        "VARCHAR": "string",
        "BINARY": "binary",
        "VARIANT": "variant",
    }
    if base not in categories:
        raise ValueError(f"unknown Snowflake type {target_type!r}")
    return categories[base]
