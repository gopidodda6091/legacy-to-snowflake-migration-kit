"""Synthetic legacy data for Bayou Retail (fictional), driven by the sample DDL.

Values are generated from each column's mapped type plus a few column-name
hints, so foreign keys line up and text looks plausible. Output is one
JSON-lines file per table, written the way a legacy export would look:
decimals and timestamps as strings, binary as hex, nested Hive types as JSON.
"""

from __future__ import annotations

import json
import random
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from migrate.translate import Column, TableDef
from migrate.typemap import category, number_spec

ROW_COUNTS = {
    "STORES": 25,
    "PRODUCTS": 120,
    "CUSTOMERS": 300,
    "SUPPLIERS": 30,
    "PURCHASE_ORDERS": 200,
    "INVENTORY_SNAPSHOTS": 300,
    "SALES_TRANSACTIONS": 400,
    "SALES_LINES": 600,
    "PRODUCT_RETURNS": 60,
    "WEB_SESSIONS": 200,
    "LOYALTY_EVENTS": 300,
    "PROMOTIONS": 20,
}
FOREIGN_KEYS = {
    "STORE_ID": "STORES",
    "PRODUCT_ID": "PRODUCTS",
    "CUSTOMER_ID": "CUSTOMERS",
    "SUPPLIER_ID": "SUPPLIERS",
}
STRING_IDS = {"TXN_ID": "T", "RETURN_ID": "R", "SESSION_ID": "S", "EVENT_ID": "E", "PROMO_ID": "P"}
CHOICES: dict[str, list[str]] = {
    "CITY": ["Lafayette", "Houma", "Thibodaux", "Lake Charles", "Biloxi", "Mobile", "Baton Rouge"],
    "STATE_CODE": ["LA", "MS", "AL", "TX"],
    "COUNTRY_CODE": ["US", "MX", "CA", "VN"],
    "CATEGORY": ["Grocery", "Hardware", "Outdoor", "Home", "Apparel", "Pharmacy"],
    "LOYALTY_TIER": ["BRONZE", "SILVER", "GOLD", "PELICAN"],
    "STATUS": ["OPEN", "RECEIVED", "PARTIAL", "CANCELLED"],
    "PAYMENT_TYPE": ["card", "cash", "gift_card", "mobile"],
    "REASON_CODE": ["DAMAGED", "WRONG_SIZE", "NOT_NEEDED", "LATE"],
    "EVENT_TYPE": ["earn", "redeem", "expire", "bonus"],
    "IS_ACTIVE": ["Y", "N"],
    "FIRST_NAME": ["Amélie", "Boudreaux", "Cora", "Dang", "Etienne", "Fern", "Giselle", "Hùng"],
    "LAST_NAME": ["Arceneaux", "Broussard", "Comeaux", "Doucet", "Fontenot", "Guidry", "Nguyễn"],
    "PAGES": ["/", "/deals", "/cart", "/checkout", "/store-locator", "/crawfish-season"],
    "CHANNELS": ["email", "app", "in_store", "web", "radio"],
}
FIELD_CHOICES: dict[str, list[str]] = {
    "os": ["iOS", "Android", "Windows", "macOS"],
    "browser": ["Safari", "Chrome", "Firefox", "Edge"],
    "utm_source": ["newsletter", "search", "social", "radio"],
    "utm_campaign": ["mardi-gras", "crawfish-boil", "back-to-school", "hurricane-prep"],
    "utm_medium": ["email", "cpc", "organic"],
    "channel": ["app", "web", "in_store"],
    "promo_code": ["BAYOU10", "LAGNIAPPE", "GUMBO5"],
}
MAP_KEYS = {
    "UTM": ["utm_source", "utm_campaign", "utm_medium"],
    "METADATA": ["channel", "promo_code"],
}
WORDS = "bayou cypress pelican levee gumbo pirogue marsh delta crawfish magnolia".split()
START = datetime(2026, 3, 1)


def _pick(rng: random.Random, key: str) -> str:
    return rng.choice(CHOICES[key])


def _decimal(rng: random.Random, precision: int, scale: int) -> str:
    int_digits = min(precision - scale, 6)
    value = Decimal(rng.randint(0, 10**int_digits - 1)) + Decimal(rng.random())
    return str(value.quantize(Decimal(1).scaleb(-scale)))


def _timestamp(rng: random.Random, col: Column) -> str:
    ts = START + timedelta(seconds=rng.randint(0, 60 * 60 * 24 * 30))
    scale = int(col.target_type.split("(")[1].rstrip(")")) if "(" in col.target_type else 6
    if scale > 0:
        ts += timedelta(microseconds=rng.randint(0, 999) * 1000)
    fraction = f".{ts.microsecond:06d}"[: scale + 1] if scale else ""
    text = ts.strftime("%Y-%m-%d %H:%M:%S") + fraction
    if category(col.target_type) == "timestamp_tz":
        text = text.replace(" ", "T") + rng.choice(["-05:00", "-06:00"])
    return text


def _variant(rng: random.Random, col: Column) -> Any:
    kind = col.source_type.split("<", 1)[0].upper()
    if kind == "ARRAY":
        pool = CHOICES.get(col.name, WORDS)
        return rng.sample(pool, rng.randint(0, min(4, len(pool))))
    if kind == "STRUCT":
        fields = re.findall(r"(\w+)\s+(\w+)", col.source_type.split("<", 1)[1])
        return {
            name: (rng.random() < 0.4)
            if ftype.upper() == "BOOLEAN"
            else rng.choice(FIELD_CHOICES.get(name, WORDS))
            for name, ftype in fields
        }
    keys = MAP_KEYS.get(col.name, WORDS)
    chosen = rng.sample(keys, rng.randint(0, len(keys)))
    return {k: rng.choice(FIELD_CHOICES.get(k, WORDS)) for k in chosen}


def _string(rng: random.Random, col: Column, row_no: int, table: str) -> str:
    name = col.name
    if name in CHOICES:
        return _pick(rng, name)
    if name in STRING_IDS:
        owner = {"TXN_ID": "SALES_TRANSACTIONS"}.get(name, table)
        n = row_no if owner == table else rng.randint(1, ROW_COUNTS[owner])
        return f"{STRING_IDS[name]}{n:07d}"
    if name.endswith("_DATE"):
        return (START + timedelta(days=rng.randint(0, 6))).strftime("%Y-%m-%d")
    if name.endswith("_MONTH"):
        return f"2026-0{rng.randint(1, 3)}"
    if name == "EMAIL":
        return f"member{row_no:05d}@example.com"
    if name == "SKU":
        return f"BR-{row_no:06d}"
    text = " ".join(rng.choice(WORDS) for _ in range(rng.randint(2, 6))).title()
    limit = col.target_type.split("(")[1].rstrip(")") if "(" in col.target_type else None
    return text[: int(limit)] if limit else text


def _value(rng: random.Random, col: Column, row_no: int, table: TableDef) -> Any:
    cat = category(col.target_type)
    if cat == "int":
        if col.name in table.primary_key and len(table.primary_key) == 1:
            return row_no
        if col.name in FOREIGN_KEYS:
            return rng.randint(1, ROW_COUNTS[FOREIGN_KEYS[col.name]])
        precision, _ = number_spec(col.target_type) or (9, 0)
        return rng.randint(0, 10 ** min(precision, 4) - 1)
    if cat == "decimal":
        precision, scale = number_spec(col.target_type) or (38, 10)
        return _decimal(rng, precision, min(scale, 4) if precision == 38 else scale)
    if cat == "float":
        return round(rng.uniform(0, 5), 4)
    if cat == "bool":
        return rng.random() < 0.5
    if cat == "date":
        return (date(2026, 1, 1) + timedelta(days=rng.randint(0, 120))).isoformat()
    if cat in {"timestamp_ntz", "timestamp_tz"}:
        return _timestamp(rng, col)
    if cat == "binary":
        return rng.randbytes(16).hex()
    if cat == "variant":
        return _variant(rng, col)
    return _string(rng, col, row_no, table.name)


def generate_rows(table: TableDef, n_rows: int, seed: int) -> list[dict[str, Any]]:
    """Generate ``n_rows`` deterministic rows; composite primary keys stay unique."""
    rng = random.Random(f"{seed}:{table.fqn}")
    rows: list[dict[str, Any]] = []
    seen_keys: set[tuple[Any, ...]] = set()
    row_no = 0
    while len(rows) < n_rows:
        row_no += 1
        row = {}
        for col in table.columns:
            nullable = col.nullable and col.name not in table.primary_key
            is_partition = col.name in table.partition_columns
            if nullable and not is_partition and rng.random() < 0.04:
                row[col.name] = None
            else:
                row[col.name] = _value(rng, col, row_no, table)
        key = tuple(row[k] for k in table.primary_key)
        if table.primary_key and key in seen_keys:
            continue
        seen_keys.add(key)
        rows.append(row)
    return rows


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def data_path(data_dir: Path, table: TableDef) -> Path:
    return data_dir / f"{table.schema.lower()}__{table.name.lower()}.jsonl"


def generate_all(tables: list[TableDef], data_dir: Path, seed: int = 42) -> list[Path]:
    """Write sample data for every table; returns the files written."""
    paths = []
    for table in tables:
        path = data_path(data_dir, table)
        write_jsonl(generate_rows(table, ROW_COUNTS.get(table.name, 50), seed), path)
        paths.append(path)
    return paths
