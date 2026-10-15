-- Bayou Retail loyalty programme events (synthetic)
CREATE TABLE IF NOT EXISTS bayou_web.loyalty_events (
    event_id      STRING,
    customer_id   BIGINT,
    event_type    STRING,
    points        INT,
    event_ts      TIMESTAMP,
    metadata      MAP<STRING,STRING>
)
PARTITIONED BY (event_month STRING)
STORED AS ORC;
