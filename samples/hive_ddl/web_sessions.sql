-- Bayou Retail e-commerce: web sessions with nested attributes (synthetic)
CREATE EXTERNAL TABLE IF NOT EXISTS bayou_web.web_sessions (
    session_id    STRING,
    customer_id   BIGINT,
    started_at    TIMESTAMP,
    pages         ARRAY<STRING>,
    utm           MAP<STRING,STRING>,
    device        STRUCT<os:STRING, browser:STRING, is_mobile:BOOLEAN>,
    duration_sec  INT
)
PARTITIONED BY (event_date STRING)
STORED AS PARQUET;
