-- Bayou Retail POS: returns (synthetic)
CREATE EXTERNAL TABLE IF NOT EXISTS bayou_sales.product_returns (
    return_id      STRING,
    txn_id         STRING,
    product_id     BIGINT,
    return_ts      TIMESTAMP,
    reason_code    VARCHAR(16),
    refund_amount  DECIMAL(10,2),
    restocked      BOOLEAN
)
PARTITIONED BY (return_date STRING)
STORED AS PARQUET;
