# Cutover checklist

A table is migrated when its reconciliation passes on the final delta and its
readers have moved. Until then the legacy system stays the source of truth.
Work through this list per wave of tables (for Bayou Retail: ERP masters
first, then POS facts, then web/loyalty).

## T-2 weeks: prepare

- [ ] `migrate translate` run on the final DDL; every `-- REVIEW` line read and
      either accepted or fixed in `typemap.py` (re-run tests after any change)
- [ ] Objects flagged `MANUAL REWRITE` (PL/SQL, Hive UDFs) have an owner and a
      Snowflake replacement (task, stored procedure, dbt model)
- [ ] Unconstrained Oracle `NUMBER` columns profiled (`MAX(scale)`), mapped
      precision confirmed
- [ ] Oracle `DATE` columns checked: does any reader rely on a midnight-only
      value? (they arrive as `TIMESTAMP_NTZ(0)`)
- [ ] Stage, file format and storage integration created
      (`templates/snowflake/file_format.sql`, `stage.sql`)
- [ ] Full historical load done with `COPY INTO`; reconciliation PASS for every
      table in the wave
- [ ] Downstream queries translated (`translate_query`) and compared on a
      sample of business KPIs, not only row-level checks

## T-1 day: rehearse

- [ ] Dry run of the delta load against yesterday's change window
- [ ] Reconciliation on the rehearsal: PASS, timing recorded
- [ ] Rollback steps below rehearsed once by someone other than the author

## T-0: cutover window

1. **Freeze.** Stop writers to the legacy tables in scope (disable ETL
   schedules, put the source in read-only or quiesce the application). Record
   the freeze timestamp and the source high-water mark per table.
2. **Delta load.** Extract rows changed since the full load (by high-water
   mark or by partition for Hive tables) and `COPY INTO` the target. For
   partitioned Hive tables, reload whole partitions rather than merging rows.
3. **Reconcile.** Run `migrate reconcile` for the wave. Every table must PASS:
   row count, row checksum, and per-column checksum, null and distinct
   counts. A single FAIL stops the cutover; go to *Rollback*.
4. **Switch readers.** Repoint BI connections, extracts and service accounts
   to Snowflake. Grant reader roles on the new schemas; revoke on legacy.
5. **Smoke test.** Owners of the top dashboards confirm their headline numbers
   against the frozen legacy values.
6. **Unfreeze writers** against the new target only.

## Rollback plan

Rollback is only cheap while legacy is still frozen and untouched.

- Trigger: any reconciliation FAIL, a failed smoke test, or a reader that
  cannot be switched within the window.
- Steps: repoint readers back to legacy (connection strings are kept in one
  config change), unfreeze legacy writers, leave Snowflake tables in place for
  diagnosis, file the reconciliation report with the incident.
- After writers resume on Snowflake, rollback means a reverse migration; treat
  the "unfreeze" step as the point of no return and get explicit sign-off.

## T+1 week: decommission

- [ ] Legacy tables renamed / made read-only, not dropped
- [ ] Reconciliation report archived with the change record
- [ ] Legacy ETL schedules removed
- [ ] Drop legacy objects after the agreed retention period
