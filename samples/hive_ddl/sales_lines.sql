-- Bayou Retail POS: transaction lines (synthetic)
CREATE EXTERNAL TABLE IF NOT EXISTS bayou_sales.sales_lines (
    txn_id        STRING,
    line_no       TINYINT,
    product_id    BIGINT,
    quantity      INT,
    unit_price    DECIMAL(10,2),
    discount_pct  DECIMAL(5,2)
)
PARTITIONED BY (sale_date STRING)
CLUSTERED BY (txn_id) INTO 8 BUCKETS
STORED AS ORC
LOCATION 'hdfs:///warehouse/bayou_sales/sales_lines'
TBLPROPERTIES ('orc.compress'='SNAPPY');
