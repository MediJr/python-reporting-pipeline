"""Pipeline orchestration: ``check_sources`` (read-only) and ``run_pipeline`` (full run).

Order of a run:

1. take the lock (one run of a project at a time) and create the run folder;
2. read and check the three CSV sources (file, header, types, keys, duplicates);
3. open the warehouse and check that it belongs to the pipeline;
4. in **one transaction**: rebuild the staging area and the star schema, load the accepted rows,
   reject lines that break referential integrity, generate the calendar, load the fact table, build
   the marts, run the quality gates and reconciliation checks, store the KPIs. Any failure rolls back
   everything, so the previous model stays intact;
5. export the Power BI files (written aside, verified, then swapped in);
6. write the run reports and record the run in ``rp_runs``; release the lock.

Re-running with the same sources rebuilds the same model and byte-identical exports: no duplicates,
no accumulation (only the run history grows).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import os
import platform
import time
import traceback
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .config import SOURCE_TABLES, Config
from .errors import ConsistencyError, PipelineError, QualityGateError, SourceError
from .exports import export_powerbi
from .log import JsonLinesHandler, get_logger
from .quality import Check, gate_checks, model_checks
from .reporting import render_quality_report
from .sources import RAW, SPECS, Rejection, SourceResult, read_source
from .transform import DIM_DATE_COLUMNS, date_dimension, derive_kpis
from .warehouse import Warehouse, open_warehouse

EXIT_SUCCESS, EXIT_FAILED, EXIT_USAGE, EXIT_REFUSED = 0, 1, 2, 3
LOCK_NAME = ".lock"
STAGING_COLUMNS = {
    "products": (
        "product_id",
        "product_name",
        "category",
        "unit_cost_cents",
        "list_price_cents",
        "launch_date",
        "source_line",
    ),
    "orders": (
        "order_id",
        "order_date",
        "customer_id",
        "customer_segment",
        "region",
        "sales_channel",
        "order_status",
        "source_line",
    ),
    "order_lines": (
        "order_id",
        "line_number",
        "product_id",
        "quantity",
        "unit_price_cents",
        "discount_bp",
        "source_line",
    ),
}


class RunRefused(PipelineError):
    """The run could not start (e.g. another run holds the lock); nothing was done."""


@dataclass
class RunResult:
    run_id: str
    status: str
    exit_code: int
    run_dir: Path | None
    report: dict[str, Any] = field(default_factory=dict)


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def check_sources(config: Config) -> dict[str, SourceResult]:
    """Read every source and apply the row checks, without touching any database or output folder."""
    return {
        table: read_source(SPECS[table], config.source_files[table], config.encoding, config.delimiter)
        for table in SOURCE_TABLES
    }


class _Run:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.run_id = str(uuid.uuid4())
        self.started = _now()
        self.clock = time.perf_counter()
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        self.run_dir = config.runs_dir / f"{stamp}_{self.run_id[:8]}"
        self.logger = get_logger()
        self.sources: dict[str, SourceResult] = {}
        self.rejections: list[Rejection] = []
        self.checks: list[Check] = []
        self.kpis: list[dict] = []
        self.model_counts: dict[str, int] = {}
        self.exports: list[dict] = []
        self.steps: list[dict] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def step(self, name: str):
        run = self

        class _Step:
            def __enter__(self):
                self.start = time.perf_counter()
                run.logger.info("step started: %s", name, extra={"data": {"step": name}})
                return self

            def __exit__(self, exc_type, exc, tb):
                duration = round(time.perf_counter() - self.start, 3)
                status = "success" if exc is None else "failed"
                run.steps.append({"step": name, "status": status, "duration_s": duration})
                run.logger.info(
                    "step %s: %s",
                    status,
                    name,
                    extra={"data": {"step": name, "status": status, "duration_s": duration}},
                )
                return False

        return _Step()

    # ------------------------------------------------------------------ steps

    def read_sources(self) -> None:
        for table in SOURCE_TABLES:
            with self.step(f"read_{table}"):
                result = read_source(
                    SPECS[table], self.config.source_files[table], self.config.encoding, self.config.delimiter
                )
                self.sources[table] = result
                self.rejections.extend(result.rejected)
                self.warnings.extend(result.warnings)
                self.logger.info(
                    "source %s: %d read, %d accepted, %d rejected",
                    table,
                    result.rows_read,
                    len(result.accepted),
                    len(result.rejected),
                    extra={
                        "data": {
                            "table": table,
                            "read": result.rows_read,
                            "accepted": len(result.accepted),
                            "rejected": len(result.rejected),
                        }
                    },
                )

    def build(self, w: Warehouse) -> None:
        w.begin()
        try:
            with self.step("create_schema"):
                w.run_file("10_schema.sql")
            with self.step("load_staging"):
                for table in SOURCE_TABLES:
                    columns = STAGING_COLUMNS[table]
                    w.insert_rows(
                        f"stg_{table}", columns, ([row[c] for c in columns] for row in self.sources[table].accepted)
                    )
                w.insert_rows(
                    "rejected_rows",
                    ("source_table", "source_line", "reason", "column_name", "message"),
                    ((r.table, r.line, r.reason, r.column, r.message) for r in self.rejections),
                )
            with self.step("referential_integrity"):
                before = len(self.rejections)
                w.run_file("20_integrity.sql")
                columns, rows = w.query(
                    "SELECT source_table, source_line, reason, column_name, message FROM rejected_rows "
                    "WHERE reason IN ('unknown_order', 'unknown_product') ORDER BY source_line"
                )
                raw_lines = {r["source_line"]: r for r in self.sources["order_lines"].accepted}
                for table, line, reason, column, message in rows:
                    raw = raw_lines[line][RAW] if line in raw_lines else {}
                    self.rejections.append(Rejection(table, line, reason, column, message, raw))
                integrity_rejected = len(self.rejections) - before
            with self.step("build_model"):
                dates = [r["order_date"] for r in self.sources["orders"].accepted]
                if not dates:
                    raise SourceError("no valid order: nothing to report")
                calendar = date_dimension(dt.date.fromisoformat(min(dates)), dt.date.fromisoformat(max(dates)))
                w.insert_rows("dim_date", DIM_DATE_COLUMNS, calendar)
                w.run_file("30_model.sql")
            with self.step("build_marts"):
                w.run_file("40_marts.sql")
            with self.step("quality_checks"):
                read = {t: self.sources[t].rows_read for t in SOURCE_TABLES}
                rejected = Counter(r.table for r in self.rejections)
                self.checks = gate_checks(read, rejected, self.config.max_rejected_rate)
                orders = {r["order_id"]: r for r in self.sources["orders"].accepted}
                self.checks += model_checks(w, self.sources["order_lines"].accepted, orders, integrity_rejected)
                failed = [c for c in self.checks if not c.passed]
                for check in failed:
                    self.logger.error(
                        "check failed: %s (expected %s, got %s)",
                        check.name,
                        check.expected,
                        check.actual,
                        extra={"data": check.as_dict()},
                    )
                if any(c.category == "gate" for c in failed):
                    raise QualityGateError(
                        "quality gate failed: "
                        + "; ".join(
                            f"{c.name} {c.actual} (limit {c.expected}, {c.detail})"
                            for c in failed
                            if c.category == "gate"
                        )
                    )
                if failed:
                    raise ConsistencyError("reconciliation failed: " + ", ".join(c.name for c in failed))
            with self.step("compute_kpis"):
                self.compute_kpis(w)
            for table in (
                "dim_date",
                "dim_product",
                "fact_sales",
                "mart_monthly_sales",
                "mart_category_monthly",
                "mart_product_performance",
                "mart_segment_region",
                "kpi_summary",
                "rejected_rows",
            ):
                self.model_counts[table] = w.scalar(f"SELECT COUNT(*) FROM {table}")  # noqa: S608 - fixed names
            w.commit()
            self.logger.info("model committed", extra={"data": self.model_counts})
        except BaseException:
            w.rollback()
            self.logger.error("transaction rolled back: the previous model is unchanged")
            raise

    def compute_kpis(self, w: Warehouse) -> None:
        columns, rows = w.query("SELECT * FROM mart_monthly_sales ORDER BY month_key")
        monthly = [dict(zip(columns, r, strict=True)) for r in rows]
        totals = {
            "active_customers": w.scalar("SELECT COUNT(DISTINCT customer_id) FROM fact_sales WHERE is_completed = 1")
        }
        top_category = w.query(
            "SELECT category, SUM(net_sales_cents) AS net FROM mart_category_monthly GROUP BY category "
            "ORDER BY net DESC, category LIMIT 1"
        )[1]
        top_product = w.query(
            "SELECT product_name, net_sales_cents FROM mart_product_performance ORDER BY sales_rank, product_id LIMIT 1"
        )[1]
        kpis = derive_kpis(
            monthly, totals, top_category[0] if top_category else None, top_product[0] if top_product else None
        )
        w.insert_rows(
            "kpi_summary",
            ("position", "kpi", "label", "value", "unit", "period", "definition"),
            ((i, k.kpi, k.label, k.value, k.unit, k.period, k.definition) for i, k in enumerate(kpis, 1)),
        )
        self.kpis = [k.__dict__ for k in kpis]

    def export(self, w: Warehouse) -> None:
        with self.step("export_powerbi"):
            manifest = {
                "run_id": self.run_id,
                "generated_at": _now(),
                "project": self.config.name,
                "engine": self.config.engine,
                "format": {
                    "encoding": "utf-8",
                    "bom": False,
                    "delimiter": ",",
                    "decimal_separator": ".",
                    "line_terminator": "LF",
                    "dates": "YYYY-MM-DD",
                    "currency": "EUR",
                    "amounts": "*_cents columns exported as decimal amounts without the suffix",
                    "ratios": "*_bp columns exported as ratios (0.1500 = 15 %) without the suffix",
                },
                "sources": {
                    t: {"file": self.config.relative(s.path), "sha256": s.sha256} for t, s in self.sources.items()
                },
            }
            checks_csv = _csv(
                ["name", "category", "passed", "expected", "actual", "detail"],
                [[c.name, c.category, str(c.passed).lower(), c.expected, c.actual, c.detail] for c in self.checks],
            )
            files = export_powerbi(w, self.config.powerbi_dir, manifest, {"quality_checks.csv": checks_csv})
            self.exports = [
                {
                    "file": f"{self.config.relative(self.config.powerbi_dir)}/{f.name}",
                    "rows": f.rows,
                    "sha256": f.sha256,
                }
                for f in files
            ]

    # ------------------------------------------------------------------ report

    def report(self, status: str, exit_code: int) -> dict[str, Any]:
        reasons = Counter((r.table, r.reason) for r in self.rejections)
        return {
            "report_version": 1,
            "tool": {"name": "python-reporting-pipeline", "version": __version__},
            "project": self.config.name,
            "run_id": self.run_id,
            "started_at": self.started,
            "finished_at": _now(),
            "duration_s": round(time.perf_counter() - self.clock, 3),
            "status": status,
            "exit_code": exit_code,
            "engine": self.config.engine,
            "sources": {
                t: {
                    "file": self.config.relative(s.path),
                    "sha256": s.sha256,
                    "rows_read": s.rows_read,
                    "accepted_by_csv_checks": len(s.accepted),
                    "normalized_values": s.normalized_values,
                }
                for t, s in self.sources.items()
            },
            "rejections": {
                "total": len(self.rejections),
                "by_table": dict(Counter(r.table for r in self.rejections)),
                "by_reason": [{"table": t, "reason": r, "rows": n} for (t, r), n in sorted(reasons.items())],
            },
            "model_rows": self.model_counts,
            "checks": [c.as_dict() for c in self.checks],
            "kpis": self.kpis,
            "exports": self.exports,
            "steps": self.steps,
            "errors": self.errors,
            "warnings": self.warnings,
            "environment": {"python": platform.python_version(), "platform": platform.platform()},
        }

    def write_reports(self, report: dict[str, Any]) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "run_report.json").write_bytes(
            (json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        )
        (self.run_dir / "quality_report.md").write_bytes(render_quality_report(report).encode("utf-8"))
        (self.run_dir / "rejected_rows.csv").write_bytes(
            _csv(
                ["source_table", "source_line", "reason", "column", "message", "raw_values"],
                [
                    [r.table, r.line, r.reason, r.column or "", r.message, json.dumps(r.raw, ensure_ascii=False)]
                    for r in sorted(self.rejections, key=lambda r: (r.table, r.line, r.reason))
                ],
            )
        )


def _csv(header: list[str], rows: list[list[Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _acquire_lock(config: Config, run_id: str) -> Path:
    config.runs_dir.mkdir(parents=True, exist_ok=True)
    lock = config.runs_dir / LOCK_NAME
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RunRefused(
            f"another run holds the lock {config.relative(lock)}; if no run is active (e.g. after a crash), delete it"
        ) from None
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"run_id={run_id} pid={os.getpid()} started={_now()}\n")
    return lock


def run_pipeline(config: Config) -> RunResult:
    """Run the whole pipeline. Expected problems give status 'failed' and a report, never a traceback."""
    run = _Run(config)
    lock = _acquire_lock(config, run.run_id)
    run.run_dir.mkdir(parents=True, exist_ok=False)
    handler = JsonLinesHandler(run.run_dir / "pipeline.log.jsonl", run.run_id)
    run.logger.addHandler(handler)
    warehouse: Warehouse | None = None
    owned = False  # history is only written to a database the pipeline owns
    status, exit_code = "failed", EXIT_FAILED
    try:
        run.logger.info("run started", extra={"data": {"project": config.name, "engine": config.engine}})
        run.read_sources()
        with run.step("connect_warehouse"):
            warehouse = open_warehouse(config)
            warehouse.prepare()
            owned = True
        run.build(warehouse)
        run.export(warehouse)
        status = "success_with_rejections" if run.rejections else "success"
        exit_code = EXIT_SUCCESS
    except PipelineError as exc:
        run.errors.append(f"{type(exc).__name__}: {exc}")
        run.logger.error("run failed: %s", exc)
    except Exception as exc:  # unexpected: recorded with its traceback, never hidden
        run.errors.append(f"internal error: {type(exc).__name__}: {exc}")
        run.logger.error("internal error", extra={"data": {"traceback": traceback.format_exc()}})
    finally:
        report = run.report(status, exit_code)
        try:
            run.write_reports(report)
        finally:
            if warehouse is not None:
                if owned:
                    _record_run(warehouse, report)
                warehouse.close()
            run.logger.info("run finished: %s", status, extra={"data": {"duration_s": report["duration_s"]}})
            run.logger.removeHandler(handler)
            handler.close()
            lock.unlink(missing_ok=True)
    return RunResult(run.run_id, status, exit_code, run.run_dir, report)


def _record_run(warehouse: Warehouse, report: dict[str, Any]) -> None:
    """Append the run to the history table (best effort: a failure here never changes the run status)."""
    try:
        warehouse.begin()
        warehouse.insert_rows(
            "rp_runs",
            ("run_id", "started_at", "finished_at", "status", "fact_rows", "rejected_rows", "message"),
            [
                (
                    report["run_id"],
                    report["started_at"],
                    report["finished_at"],
                    report["status"],
                    report["model_rows"].get("fact_sales"),
                    report["rejections"]["total"],
                    "; ".join(report["errors"])[:1000] or None,
                )
            ],
        )
        warehouse.commit()
    except Exception:
        warehouse.rollback()
        logging.getLogger(__name__).warning("run history could not be recorded")
