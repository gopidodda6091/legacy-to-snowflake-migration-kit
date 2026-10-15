-- Bayou Retail POS: transaction headers (synthetic)
CREATE EXTERNAL TABLE IF NOT EXISTS bayou_sales.sales_transactions (
    txn_id        STRING COMMENT 'POS transaction id',
    store_id      INT,
    customer_id   BIGINT,
    txn_ts        TIMESTAMP,
    payment_type  STRING,
    total_amount  DECIMAL(12,2),
    item_count    SMALLINT
)
PARTITIONED BY (sale_date STRING)
STORED AS PARQUET
LOCATION 'hdfs:///warehouse/bayou_sales/sales_transactions';
