"""Command line: ``python -m migrate.cli <command>`` (or the ``migrate`` script).

Commands map one-to-one to the migration steps::

    generate   synthetic legacy data for the sample DDL
    translate  legacy DDL -> Snowflake DDL (+ flags procedural code)
    extract    legacy data -> typed, partitioned Parquet
    load       Parquet -> DuckDB, and render Snowflake COPY INTO scripts
    reconcile  source vs target profiles -> markdown report (exit 1 on FAIL)
    demo       all of the above in one go
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import duckdb

from migrate import extract as ex
from migrate import load as ld
from migrate import reconcile as rc
from migrate import synth
from migrate.translate import TableDef, load_tables, write_snowflake_ddl

DDL_DIRS = {"oracle": "oracle_ddl", "hive": "hive_ddl"}


def discover(samples: Path) -> tuple[list[TableDef], list[Path]]:
    """All tables from ``<samples>/{oracle_ddl,hive_ddl}`` plus flagged files."""
    tables: list[TableDef] = []
    flagged: list[Path] = []
    for dialect, sub in DDL_DIRS.items():
        result = load_tables(samples / sub, dialect)
        tables += result.tables
        flagged += result.flagged
    return tables, flagged


def select(tables: list[TableDef], names: Sequence[str] | None) -> list[TableDef]:
    if not names:
        return tables
    wanted = {n.upper() for n in names}
    return [t for t in tables if t.name in wanted or t.fqn in wanted]


def cmd_generate(args: argparse.Namespace) -> int:
    tables, _ = discover(args.samples)
    paths = synth.generate_all(tables, args.data, seed=args.seed)
    print(f"generated {len(paths)} tables into {args.data}")
    return 0


def cmd_translate(args: argparse.Namespace) -> int:
    tables, flagged = discover(args.samples)
    paths = write_snowflake_ddl(tables, args.out / "ddl", args.database)
    print(f"translated {len(paths)} tables into {args.out / 'ddl'}")
    for t in tables:
        for w in t.warnings:
            print(f"  REVIEW {w}")
    for f in flagged:
        print(f"  MANUAL REWRITE {f.name} (procedural code is out of scope)")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    tables, _ = discover(args.samples)
    for t in select(tables, args.tables):
        rows = ex.read_jsonl(synth.data_path(args.data, t))
        out = ex.extract_table(t, rows, args.extract)
        print(f"extracted {t.fqn}: {len(rows)} rows, {len(ex.parquet_files(out))} files")
    return 0


def _load_all(
    tables: list[TableDef], extract_root: Path, db: Path, lost_file_tables: set[str]
) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db)) as con:
        for t in tables:
            src = extract_root / t.schema.lower() / t.name.lower()
            flt = ld.drop_first_file(src) if t.name in lost_file_tables else None
            n = ld.load_table(con, t, src, file_filter=flt)
            note = "  (simulated lost file)" if flt else ""
            print(f"loaded {t.fqn}: {n} rows{note}")


def cmd_load(args: argparse.Namespace) -> int:
    tables, _ = discover(args.samples)
    chosen = select(tables, args.tables)
    _load_all(chosen, args.extract, args.db, set())
    ld.write_copy_scripts(chosen, args.out / "copy", args.database)
    print(f"rendered COPY INTO scripts into {args.out / 'copy'}")
    return 0


def _reconcile(
    tables: list[TableDef], data: Path, db: Path, report: Path, notes: dict[str, str]
) -> list[rc.TableResult]:
    results = []
    with duckdb.connect(str(db), read_only=True) as con:
        for t in tables:
            source = rc.profile_source(t, ex.read_jsonl(synth.data_path(data, t)))
            result = rc.compare(t, source, rc.profile_duckdb(con, t))
            result.note = notes.get(t.name, "")
            results.append(result)
    rc.write_report(results, report)
    passed = sum(r.passed for r in results)
    failed = len(results) - passed
    print(f"reconciled {len(results)} tables: {passed} PASS, {failed} FAIL -> {report}")
    return results


def cmd_reconcile(args: argparse.Namespace) -> int:
    tables, _ = discover(args.samples)
    results = _reconcile(select(tables, args.tables), args.data, args.db, args.report, {})
    return 0 if all(r.passed for r in results) else 1


def cmd_demo(args: argparse.Namespace) -> int:
    """Translate, extract, load and reconcile every sample table.

    ``--simulate-lost-file`` drops one Parquet file of the named tables during
    the load. Those tables must FAIL; the demo exits non-zero if the set of
    failing tables differs from the set of sabotaged ones.
    """
    lost = {n.upper() for n in args.simulate_lost_file}
    cmd_translate(args)
    cmd_extract(argparse.Namespace(**{**vars(args), "tables": None}))
    tables, _ = discover(args.samples)
    if args.db.exists():
        args.db.unlink()
    _load_all(tables, args.extract, args.db, lost)
    ld.write_copy_scripts(tables, args.out / "copy", args.database)
    notes = {n: "Deliberate mismatch: one Parquet file was skipped during load." for n in lost}
    results = _reconcile(tables, args.data, args.db, args.report, notes)
    failed = {r.table.split(".")[-1] for r in results if not r.passed}
    if failed != lost:
        print(f"unexpected reconciliation outcome: failed={sorted(failed)} expected={sorted(lost)}")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="migrate", description=__doc__.splitlines()[0])
    parser.add_argument("--samples", type=Path, default=Path("samples"))
    parser.add_argument("--data", type=Path, default=Path("samples/data"))
    parser.add_argument("--extract", type=Path, default=Path("build/extract"))
    parser.add_argument("--db", type=Path, default=Path("build/target.duckdb"))
    parser.add_argument("--out", type=Path, default=Path("build/snowflake"))
    parser.add_argument("--report", type=Path, default=Path("reports/reconciliation.md"))
    parser.add_argument("--database", default="BAYOU_RETAIL")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="write synthetic legacy data")
    gen.add_argument("--seed", type=int, default=42)
    gen.set_defaults(func=cmd_generate)

    sub.add_parser("translate", help="legacy DDL -> Snowflake DDL").set_defaults(func=cmd_translate)

    for name, func in (("extract", cmd_extract), ("load", cmd_load), ("reconcile", cmd_reconcile)):
        p = sub.add_parser(name)
        p.add_argument("--tables", nargs="*", help="limit to these tables (default: all)")
        p.set_defaults(func=func)

    demo = sub.add_parser("demo", help="run every step on the samples")
    demo.add_argument("--simulate-lost-file", nargs="*", default=[], metavar="TABLE")
    demo.set_defaults(func=cmd_demo)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
