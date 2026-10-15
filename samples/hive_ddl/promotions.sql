-- Bayou Retail marketing promotions (synthetic)
CREATE TABLE IF NOT EXISTS bayou_web.promotions (
    promo_id             STRING,
    promo_name           STRING,
    start_date           DATE,
    end_date             DATE,
    discount_pct         DOUBLE,
    channels             ARRAY<STRING>,
    eligible_categories  ARRAY<STRING>,
    budget               DECIMAL(12,2),
    is_stackable         BOOLEAN
)
ROW FORMAT DELIMITED FIELDS TERMINATED BY '\t'
STORED AS TEXTFILE;
