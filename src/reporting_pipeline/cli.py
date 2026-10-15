"""Command-line interface: ``reporting-pipeline check | run | generate-demo-data``."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from . import __version__, demo_data
from .config import SOURCE_TABLES, load_config
from .errors import ConfigError, SourceError
from .log import configure_console
from .pipeline import EXIT_FAILED, EXIT_REFUSED, EXIT_SUCCESS, RunRefused, check_sources, run_pipeline

EPILOG = """examples:
  reporting-pipeline check --config config/pipeline.toml
  reporting-pipeline run --config config/pipeline.toml
  reporting-pipeline generate-demo-data --output data/sample --overwrite

exit codes: 0 success, 1 run failed (nothing published), 2 usage error,
            3 refused before running (invalid configuration, unreadable sources, lock held)
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reporting-pipeline",
        description="Sales reporting pipeline: CSV sources -> checks -> SQL star schema -> KPIs -> Power BI files.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"reporting-pipeline {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="progress (-v) or details (-vv) on stderr")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for name, text in (
        ("check", "validate the configuration and the sources (read-only: no database, no output)"),
        ("run", "run the whole pipeline and publish the Power BI files"),
    ):
        sub = commands.add_parser(
            name, help=text, description=text, epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter
        )
        sub.add_argument("--config", required=True, type=Path, help="pipeline configuration file (.toml)")
    generate = commands.add_parser("generate-demo-data", help="write the deterministic synthetic demo data set")
    generate.add_argument(
        "--output", required=True, type=Path, help="folder for orders.csv, order_lines.csv, products.csv"
    )
    generate.add_argument("--seed", type=int, default=demo_data.SEED, help=f"random seed (default {demo_data.SEED})")
    generate.add_argument("--overwrite", action="store_true", help="replace existing files")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    configure_console(args.verbose)
    if args.command == "generate-demo-data":
        return _generate(args)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Result: REFUSED (invalid configuration; nothing was run)", file=sys.stderr)
        return EXIT_REFUSED
    if args.command == "check":
        return _check(config)
    try:
        result = run_pipeline(config)
    except RunRefused as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Result: REFUSED (nothing was run)", file=sys.stderr)
        return EXIT_REFUSED
    _print_run(result.report, config.relative(result.run_dir))
    return result.exit_code


def _generate(args: argparse.Namespace) -> int:
    try:
        written = demo_data.write(args.output, demo_data.generate(args.seed), overwrite=args.overwrite)
    except FileExistsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    for path in written:
        print(f"Wrote {path.as_posix()}")
    return EXIT_SUCCESS


def _check(config) -> int:
    print(f"Project: {config.name}")
    print(f"Sources: {config.relative(config.source_dir)} (connector csv, encoding {config.encoding})")
    try:
        results = check_sources(config)
    except SourceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Result: REFUSED (sources are not usable)", file=sys.stderr)
        return EXIT_REFUSED
    for table in SOURCE_TABLES:
        result = results[table]
        reasons = Counter(r.reason for r in result.rejected)
        detail = ", ".join(f"{reason} {n}" for reason, n in sorted(reasons.items())) or "none"
        print(
            f"  {table}: {result.rows_read} row(s) read, {len(result.accepted)} accepted, "
            f"{len(result.rejected)} rejected ({detail})"
        )
        for warning in result.warnings:
            print(f"  warning: {warning}")
    target = config.relative(config.sqlite_path) if config.engine == "sqlite" else f"schema {config.schema}"
    print(f"Warehouse: {config.engine} ({target}); Power BI exports: {config.relative(config.powerbi_dir)}")
    print("Note: referential integrity (unknown orders/products) is checked during 'run'.")
    print("Result: SOURCES USABLE (nothing was written)")
    return EXIT_SUCCESS


def _print_run(report: dict, run_dir: str) -> None:
    print(f"Run {report['run_id']} of {report['project']} ({report['engine']})")
    for table, source in report["sources"].items():
        print(f"  {table}: {source['rows_read']} read, {report['rejections']['by_table'].get(table, 0)} rejected")
    if report["model_rows"]:
        rows = report["model_rows"]
        print(
            f"  model: fact_sales {rows['fact_sales']} rows, dim_product {rows['dim_product']}, "
            f"dim_date {rows['dim_date']}"
        )
    failed = [c for c in report["checks"] if not c["passed"]]
    if report["checks"]:
        print(f"  checks: {len(report['checks']) - len(failed)} passed, {len(failed)} failed")
    for kpi in report["kpis"][:4]:
        print(f"  {kpi['label']}: {kpi['value']} {kpi['unit'] if kpi['unit'] != 'count' else ''}".rstrip())
    if report["exports"]:
        print(f"  exports: {len(report['exports'])} files published")
    for error in report["errors"]:
        print(f"Error: {error}", file=sys.stderr)
    print(f"Report: {run_dir}/ (run_report.json, quality_report.md, rejected_rows.csv, pipeline.log.jsonl)")
    stream = sys.stdout if report["exit_code"] == 0 else sys.stderr
    print(
        f"Result: {report['status'].upper()} (exit code {report['exit_code']}, {report['duration_s']} s)", file=stream
    )


if __name__ == "__main__":
    sys.exit(main())
