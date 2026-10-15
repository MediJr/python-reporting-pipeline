"""Warehouse connectors: SQL loading, safety rules, PostgreSQL behaviour identical to SQLite."""

from __future__ import annotations

import os
import sqlite3

import pytest
from conftest import write_config

from reporting_pipeline.config import load_config
from reporting_pipeline.errors import WarehouseError
from reporting_pipeline.pipeline import run_pipeline
from reporting_pipeline.warehouse import SQL_FILES, SqliteWarehouse, load_sql, redact, split_statements


def test_sql_files_load_for_both_dialects():
    for name in SQL_FILES:
        sqlite_sql, pg_sql = load_sql(name, "sqlite"), load_sql(name, "postgres")
        assert len(sqlite_sql) == len(pg_sql) > 0
        assert not any("{{" in s for s in sqlite_sql + pg_sql)
    schema = " ".join(load_sql("10_schema.sql", "postgres"))
    assert "order_date       DATE" in schema and "PRIMARY KEY (order_id, line_number)" in schema


def test_statement_splitting():
    text = "-- comment\nCREATE TABLE a (x INTEGER);\n\nINSERT INTO a\nVALUES (1);\n"
    assert split_statements(text) == ["CREATE TABLE a (x INTEGER)", "INSERT INTO a\nVALUES (1)"]
    with pytest.raises(WarehouseError, match="without a semicolon"):
        split_statements("SELECT 1")


def test_passwords_are_redacted_from_driver_messages():
    assert (
        redact("connection failed: host=db password=hunter2 user=x") == "connection failed: host=db password=*** user=x"
    )
    assert "hunter2" not in redact("postgresql://user:hunter2@db:5432/x")


def test_identifiers_are_validated_and_values_are_parameters(tmp_path):
    w = SqliteWarehouse(tmp_path / "w.sqlite")
    w.execute("CREATE TABLE t (name TEXT)")
    w.insert_rows("t", ["name"], [("Robert'); DROP TABLE t; --",)])
    assert w.scalar("SELECT name FROM t") == "Robert'); DROP TABLE t; --"
    with pytest.raises(WarehouseError, match="invalid identifier"):
        w.insert_rows("t; DROP TABLE t", ["name"], [("x",)])
    w.close()


def test_sqlite_ownership_marker(tmp_path):
    fresh = SqliteWarehouse(tmp_path / "new.sqlite")
    fresh.prepare()
    assert fresh.scalar("SELECT value FROM rp_metadata WHERE key = 'application'") == "python-reporting-pipeline"
    fresh.close()
    again = SqliteWarehouse(tmp_path / "new.sqlite")
    again.prepare()  # owned: accepted
    again.close()
    not_sqlite = tmp_path / "notes.sqlite"
    not_sqlite.write_text("this is not a database", encoding="utf-8")
    with pytest.raises(WarehouseError, match="not a usable SQLite database"):
        SqliteWarehouse(not_sqlite)
    assert not_sqlite.read_text(encoding="utf-8") == "this is not a database"


# --------------------------------------------------------------------------- PostgreSQL


def _pg_config(project, monkeypatch, dsn, schema="reporting"):
    monkeypatch.setenv("RP_TEST_DSN", dsn)
    return load_config(
        write_config(
            project, engine="postgres", name="pg.toml", warehouse=f'connection_env = "RP_TEST_DSN"\nschema = "{schema}"'
        )
    )


def test_postgres_run_matches_sqlite_byte_for_byte(project, monkeypatch, postgres_dsn, tmp_path_factory):
    sqlite_result = run_pipeline(load_config(project / "config" / "pipeline.toml"))
    sqlite_files = {p.name: p.read_bytes() for p in (project / "output/powerbi").glob("*.csv")}
    pg_result = run_pipeline(_pg_config(project, monkeypatch, postgres_dsn, schema="reporting_cmp"))
    assert (pg_result.status, pg_result.exit_code) == ("success_with_rejections", 0)
    assert pg_result.report["engine"] == "postgres"
    pg_files = {p.name: p.read_bytes() for p in (project / "output/powerbi").glob("*.csv")}
    assert sqlite_files == pg_files and len(pg_files) == 10
    assert sqlite_result.report["model_rows"] == pg_result.report["model_rows"]
    rerun = run_pipeline(_pg_config(project, monkeypatch, postgres_dsn, schema="reporting_cmp"))
    assert rerun.report["model_rows"]["fact_sales"] == 9774  # rebuilt, not appended


def test_postgres_refuses_a_schema_it_did_not_create(project, monkeypatch, postgres_dsn):
    import psycopg

    with psycopg.connect(postgres_dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS crm CASCADE")
        conn.execute("CREATE SCHEMA crm")
        conn.execute("CREATE TABLE crm.customers (id INTEGER)")
    result = run_pipeline(_pg_config(project, monkeypatch, postgres_dsn, schema="crm"))
    assert result.status == "failed" and "was not created by this pipeline" in result.report["errors"][0]
    with psycopg.connect(postgres_dsn) as conn:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'crm'"
            ).fetchall()
        ]
    assert tables == ["customers"]


def test_postgres_failure_rolls_back_the_transaction(project, monkeypatch, postgres_dsn):
    import psycopg

    config = _pg_config(project, monkeypatch, postgres_dsn, schema="reporting_rb")
    assert run_pipeline(config).exit_code == 0
    failing = load_config(
        write_config(
            project,
            engine="postgres",
            name="pg_strict.toml",
            lines=0.0,
            warehouse='connection_env = "RP_TEST_DSN"\nschema = "reporting_rb"',
        )
    )
    assert run_pipeline(failing).status == "failed"
    with psycopg.connect(postgres_dsn) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reporting_rb.fact_sales").fetchone()[0] == 9774
        assert conn.execute("SELECT COUNT(*) FROM reporting_rb.rp_runs").fetchone()[0] == 2


def test_postgres_connection_errors_are_clean_and_leak_no_secret(project, monkeypatch):
    pytest.importorskip("psycopg")
    dsn = "host=127.0.0.1 port=1 dbname=x user=y password=TopSecret123 connect_timeout=3"
    result = run_pipeline(_pg_config(project, monkeypatch, dsn))
    assert result.status == "failed"
    message = result.report["errors"][0]
    assert "cannot connect to PostgreSQL (connection string from $RP_TEST_DSN)" in message
    run_files = "".join(p.read_text(encoding="utf-8") for p in result.run_dir.iterdir())
    assert "TopSecret123" not in message and "TopSecret123" not in run_files
    monkeypatch.delenv("RP_TEST_DSN")
    result = run_pipeline(load_config(project / "config" / "pg.toml"))
    assert "environment variable RP_TEST_DSN is not set" in result.report["errors"][0]


def test_sqlite_database_file_is_a_real_star_schema(project):
    run_pipeline(load_config(project / "config" / "pipeline.toml"))
    with sqlite3.connect(project / "output/warehouse.sqlite") as db:
        fks = db.execute("PRAGMA foreign_key_list(fact_sales)").fetchall()
        assert {(fk[2], fk[3]) for fk in fks} == {("dim_date", "date_key"), ("dim_product", "product_id")}
        # no row multiplication: one fact row per source line key
        assert (
            db.execute("SELECT COUNT(*) FROM fact_sales").fetchone()[0]
            == db.execute("SELECT COUNT(*) FROM (SELECT DISTINCT order_id, line_number FROM fact_sales)").fetchone()[0]
        )
    assert os.path.getsize(project / "output/warehouse.sqlite") > 0
