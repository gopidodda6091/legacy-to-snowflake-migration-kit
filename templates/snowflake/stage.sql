-- External stage over the bucket that receives `migrate extract` output.
-- Replace the two placeholders; the storage integration is created by an
-- ACCOUNTADMIN once (CREATE STORAGE INTEGRATION ... ) and never holds keys here.
CREATE STAGE IF NOT EXISTS BAYOU_RETAIL.MIGRATION.LEGACY_EXTRACT_STAGE
    URL = 's3://<extract-bucket>/bayou-retail/'
    STORAGE_INTEGRATION = <storage_integration_name>
    FILE_FORMAT = BAYOU_RETAIL.MIGRATION.FF_PARQUET
    COMMENT = 'Parquet extracts: <schema>/<table>/[PARTITION=value/]part-*.parquet';

-- Sanity check before any COPY:
-- LIST @BAYOU_RETAIL.MIGRATION.LEGACY_EXTRACT_STAGE;
