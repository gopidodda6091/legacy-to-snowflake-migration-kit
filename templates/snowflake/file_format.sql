-- Parquet file format shared by every migration COPY.
-- Run once per environment, as a role that owns the MIGRATION schema.
CREATE SCHEMA IF NOT EXISTS BAYOU_RETAIL.MIGRATION;

CREATE FILE FORMAT IF NOT EXISTS BAYOU_RETAIL.MIGRATION.FF_PARQUET
    TYPE = PARQUET
    USE_LOGICAL_TYPE = TRUE        -- keep DECIMAL / TIMESTAMP logical types
    BINARY_AS_TEXT = FALSE         -- BINARY columns stay binary
    COMMENT = 'Legacy extract files written by migrate extract';
