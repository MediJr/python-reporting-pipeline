"""End-to-end runs on the demo data (SQLite): results, reruns and failure scenarios."""

from __future__ import annotations

import csv
import importlib.util
import json
import sqlite3
import uuid
from collections import Counter

import pytest
from conftest import ROOT, write_config

from reporting_pipeline import exports, warehouse
from reporting_pipeline.config import load_config
from reporting_pipeline.pipeline import RunRefused, run_pipeline


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(project, **thresholds):
    path = write_config(project, **thresholds) if thresholds else project / "config" / "pipeline.toml"
    return run_pipeline(load_config(path))


def _files(folder):
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())}


def _csv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_demo_run_publishes_verified_exports_and_reports(project):
    result = _run(project)
    assert (result.status, result.exit_code) == ("success_with_rejections", 0)
    assert uuid.UUID(result.run_id).version == 4
    report = result.report
    # every planted anomaly is caught (data/sample was generated with the anomalies of demo_data.ANOMALIES)
    reasons = Counter((r["table"], r["reason"]) for r in report["rejections"]["by_reason"] for _ in range(r["rows"]))
    assert reasons == Counter(
        {
            ("orders", "duplicate_key_conflict"): 2,
            ("orders", "duplicate_row"): 1,
            ("orders", "invalid_date"): 1,
            ("orders", "invalid_value"): 1,
            ("orders", "missing_value"): 1,
            ("order_lines", "duplicate_row"): 1,
            ("order_lines", "invalid_number"): 2,
            ("order_lines", "missing_value"): 1,
            ("order_lines", "out_of_range"): 2,
            ("order_lines", "unknown_product"): 1,
            ("order_lines", "unknown_order"): 9,  # SO-999999 + the 8 lines of the 4 rejected orders
            ("products", "duplicate_row"): 1,
        }
    )
    assert report["sources"]["orders"]["normalized_values"] == 2
    assert report["model_rows"]["fact_sales"] == 9790 - 16 == 9774
    assert all(c["passed"] for c in report["checks"]) and len(report["checks"]) == 19
    published = {p.name for p in (project / "output/powerbi").iterdir()}
    assert published == {f"{t}.csv" for t in exports.EXPORTS} | {"quality_checks.csv", "manifest.json"}
    assert sorted(p.name for p in result.run_dir.iterdir()) == [
        "pipeline.log.jsonl",
        "quality_report.md",
        "rejected_rows.csv",
        "run_report.json",
    ]
    events = [
        json.loads(line) for line in (result.run_dir / "pipeline.log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[0]["event"] == "run started" and events[-1]["event"].startswith("run finished")
    assert all(e["run_id"] == result.run_id for e in events)
    rejected = _csv(result.run_dir / "rejected_rows.csv")
    assert len(rejected) == 23 and {r["reason"] for r in rejected} >= {"unknown_order", "invalid_date"}
    unknown_product = next(r for r in rejected if r["reason"] == "unknown_product")
    assert json.loads(unknown_product["raw_values"])["unit_price"] == "34.90"  # source text, not cents
    assert not (project / "runs" / ".lock").exists()


def test_results_match_an_independent_recomputation(project):
    _run(project)
    verifier = _load_script("verify_results")
    expected = verifier.expected_results(project / "data/sample")
    checks, problems = verifier.compare(expected, project / "output/powerbi")
    assert problems == [] and checks > 60
    # a few figures checked by hand-written totals of the generated data set
    kpis = {r["kpi"]: r["value"] for r in _csv(project / "output/powerbi/kpi_summary.csv")}
    assert kpis["net_sales"] == "5069715.72" and kpis["orders"] == "3898"


def test_export_format(project):
    _run(project)
    folder = project / "output/powerbi"
    for path in folder.glob("*.csv"):
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw, path.name
    fact = _csv(folder / "fact_sales.csv")
    assert list(fact[0])[:4] == ["order_id", "line_number", "date_key", "order_date"]
    assert "net_amount" in fact[0] and "net_amount_cents" not in fact[0]
    first = fact[0]  # SO-000001 line 1: 5 x 49.00, 15 % discount
    assert (first["gross_amount"], first["discount_rate"], first["net_amount"]) == ("245.00", "0.1500", "208.25")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"]["bom"] is False and {f["name"] for f in manifest["files"]} >= {"fact_sales.csv"}
    assert all("Users" not in json.dumps(manifest) for _ in [0])  # only relative paths


def test_rerun_gives_identical_exports_and_no_duplicates(project):
    first = _run(project)
    before = _files(project / "output/powerbi")
    second = _run(project)
    after = _files(project / "output/powerbi")
    assert first.run_id != second.run_id
    assert {k: v for k, v in before.items() if k != "manifest.json"} == {
        k: v for k, v in after.items() if k != "manifest.json"
    }
    with sqlite3.connect(project / "output/warehouse.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM fact_sales").fetchone()[0] == 9774
        assert db.execute("SELECT COUNT(*) FROM rp_runs").fetchone()[0] == 2  # history grows, data does not
    assert len(list((project / "runs").iterdir())) == 2


def test_quality_gate_failure_rolls_back_and_keeps_previous_exports(project):
    _run(project)
    previous = _files(project / "output/powerbi")
    result = _run(project, lines=0.0)  # no rejected order line allowed
    assert (result.status, result.exit_code) == ("failed", 1)
    assert "quality gate failed: rejected_rate_order_lines" in result.report["errors"][0]
    assert _files(project / "output/powerbi") == previous
    with sqlite3.connect(project / "output/warehouse.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM fact_sales").fetchone()[0] == 9774  # previous model intact
        assert db.execute("SELECT status FROM rp_runs ORDER BY started_at DESC LIMIT 1").fetchone()[0] == "failed"
    assert "FAILED" in (result.run_dir / "quality_report.md").read_text(encoding="utf-8")


def test_sql_failure_in_the_middle_rolls_back_everything(project, monkeypatch):
    _run(project)
    original = warehouse.Warehouse.run_file

    def broken(self, name):
        if name == "40_marts.sql":
            raise warehouse.WarehouseError("simulated failure while building the marts")
        return original(self, name)

    monkeypatch.setattr(warehouse.Warehouse, "run_file", broken)
    result = _run(project)
    assert result.status == "failed" and "simulated failure" in result.report["errors"][0]
    with sqlite3.connect(project / "output/warehouse.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM mart_monthly_sales").fetchone()[0] == 24
        assert db.execute("SELECT COUNT(*) FROM fact_sales").fetchone()[0] == 9774


def test_export_failure_keeps_the_previous_export(project, monkeypatch):
    _run(project)
    previous = _files(project / "output/powerbi")

    def failing_verify(folder, files):
        raise exports.ExportError("simulated verification failure")

    monkeypatch.setattr(exports, "_verify", failing_verify)
    result = _run(project)
    assert result.status == "failed" and "simulated verification failure" in result.report["errors"][0]
    assert _files(project / "output/powerbi") == previous
    assert not [p for p in (project / "output").iterdir() if ".tmp-" in p.name or ".old-" in p.name]


def test_a_database_not_created_by_the_pipeline_is_never_modified(project):
    target = project / "output" / "warehouse.sqlite"
    target.parent.mkdir(parents=True)
    with sqlite3.connect(target) as db:
        db.execute("CREATE TABLE customers (id INTEGER)")
        db.execute("INSERT INTO customers VALUES (1)")
    before = target.read_bytes()
    result = _run(project)
    assert result.status == "failed" and "was not created by this pipeline" in result.report["errors"][0]
    assert target.read_bytes() == before
    assert not (project / "output/powerbi").exists()


def test_missing_source_file_fails_with_a_report(project):
    (project / "data/sample/products.csv").unlink()
    result = _run(project)
    assert result.status == "failed" and "source file not found: products.csv" in result.report["errors"][0]
    assert (result.run_dir / "run_report.json").exists()


def test_a_held_lock_refuses_the_run(project):
    (project / "runs").mkdir()
    (project / "runs" / ".lock").write_text("run_id=other", encoding="utf-8")
    with pytest.raises(RunRefused, match="another run holds the lock"):
        _run(project)


def test_unexpected_errors_are_recorded_not_hidden(project, monkeypatch):
    from reporting_pipeline import pipeline

    def boom(*args, **kwargs):
        raise ZeroDivisionError("unexpected")

    monkeypatch.setattr(pipeline, "derive_kpis", boom)
    result = _run(project)
    assert result.status == "failed" and "internal error: ZeroDivisionError" in result.report["errors"][0]
    log = (result.run_dir / "pipeline.log.jsonl").read_text(encoding="utf-8")
    assert "Traceback" in log
