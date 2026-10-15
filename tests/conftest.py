from __future__ import annotations

import pytest

from migrate.translate import TableDef, parse_table

SALES_DDL = """
CREATE TABLE bayou_sales.mini_sales (
    txn_id   STRING,
    amount   DECIMAL(10,2),
    sold_at  TIMESTAMP,
    tags     ARRAY<STRING>,
    is_promo BOOLEAN
)
PARTITIONED BY (sale_date STRING)
STORED AS PARQUET
"""

ERP_DDL = """
CREATE TABLE BAYOU_ERP.MINI_CUSTOMERS (
    CUSTOMER_ID  NUMBER(12) NOT NULL,
    NAME         NVARCHAR2(50),
    BALANCE      NUMBER(14,2),
    SIGNUP       DATE,
    LAST_SEEN    TIMESTAMP(3) WITH TIME ZONE,
    GUID         RAW(16),
    CONSTRAINT PK PRIMARY KEY (CUSTOMER_ID)
)
"""


@pytest.fixture()
def sales_table() -> TableDef:
    return parse_table(SALES_DDL, "hive")


@pytest.fixture()
def erp_table() -> TableDef:
    return parse_table(ERP_DDL, "oracle")


@pytest.fixture()
def sales_rows() -> list[dict]:
    return [
        {
            "TXN_ID": "T1",
            "AMOUNT": "10.50",
            "SOLD_AT": "2026-03-01 09:00:00.250000",
            "TAGS": ["a", "b"],
            "IS_PROMO": True,
            "SALE_DATE": "2026-03-01",
        },
        {
            "TXN_ID": "T2",
            "AMOUNT": "3.00",
            "SOLD_AT": "2026-03-01 10:00:00",
            "TAGS": [],
            "IS_PROMO": False,
            "SALE_DATE": "2026-03-01",
        },
        {
            "TXN_ID": "T3",
            "AMOUNT": None,
            "SOLD_AT": "2026-03-02 11:30:00",
            "TAGS": None,
            "IS_PROMO": None,
            "SALE_DATE": "2026-03-02",
        },
    ]


@pytest.fixture()
def erp_rows() -> list[dict]:
    return [
        {
            "CUSTOMER_ID": 1,
            "NAME": "Amélie Broussard",
            "BALANCE": "120.10",
            "SIGNUP": "2025-11-02 14:05:00",
            "LAST_SEEN": "2026-03-01T08:00:00.123-06:00",
            "GUID": "00ff" * 8,
        },
        {
            "CUSTOMER_ID": 2,
            "NAME": "",
            "BALANCE": "0.00",
            "SIGNUP": None,
            "LAST_SEEN": None,
            "GUID": None,
        },
    ]
