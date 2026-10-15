"""Shared fixtures: a copy of the demo project in a temporary folder, and a disposable PostgreSQL server."""

from __future__ import annotations

import glob
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

CONFIG = """\
[project]
name = "test-project"
root = ".."

[sources]
directory = "data/sample"

[sources.files]
orders = "orders.csv"
order_lines = "order_lines.csv"
products = "products.csv"

[warehouse]
engine = "{engine}"
{warehouse}

[outputs]
powerbi_dir = "output/powerbi"
runs_dir = "runs"

[quality]
max_rejected_rate = {{ orders = {orders}, order_lines = {lines}, products = {products} }}
"""


def write_config(
    project: Path,
    engine: str = "sqlite",
    orders: float = 0.01,
    lines: float = 0.01,
    products: float = 0.05,
    warehouse: str | None = None,
    name: str = "pipeline.toml",
) -> Path:
    if warehouse is None:
        warehouse = (
            'sqlite_path = "output/warehouse.sqlite"'
            if engine == "sqlite"
            else ('connection_env = "RP_TEST_DSN"\nschema = "reporting"')
        )
    path = project / "config" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        CONFIG.format(engine=engine, warehouse=warehouse, orders=orders, lines=lines, products=products),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project folder with the committed demo data and a SQLite configuration."""
    shutil.copytree(ROOT / "data" / "sample", tmp_path / "data" / "sample")
    write_config(tmp_path)
    return tmp_path


def _pg_binary(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    candidates = sorted(glob.glob(rf"C:\Program Files\PostgreSQL\*\bin\{name}.exe"), reverse=True)
    return candidates[0] if candidates else None


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(scope="session")
def postgres_dsn(tmp_path_factory):
    """A throwaway PostgreSQL cluster (initdb in a temp folder, random local port), or skip."""
    if os.environ.get("RP_TEST_PG_DSN"):
        yield os.environ["RP_TEST_PG_DSN"]
        return
    initdb, pg_ctl, psql = _pg_binary("initdb"), _pg_binary("pg_ctl"), _pg_binary("psql")
    if not (initdb and pg_ctl and psql):
        pytest.skip("PostgreSQL binaries (initdb, pg_ctl, psql) not found; set RP_TEST_PG_DSN to use a server")
    pytest.importorskip("psycopg")
    base = tmp_path_factory.mktemp("pg")
    data = base / "data"
    port = _free_port()
    subprocess.run(
        [initdb, "-D", str(data), "-U", "tester", "-A", "trust", "-E", "UTF8", "--no-locale"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            pg_ctl,
            "-D",
            str(data),
            "-l",
            str(base / "log.txt"),
            "-w",
            "-t",
            "60",
            "-o",
            f"-p {port} -c listen_addresses=127.0.0.1",
            "start",
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        subprocess.run(
            [
                psql,
                "-h",
                "127.0.0.1",
                "-p",
                str(port),
                "-U",
                "tester",
                "-d",
                "postgres",
                "-c",
                "CREATE DATABASE pipeline_test",
            ],
            check=True,
            capture_output=True,
        )
        yield f"host=127.0.0.1 port={port} dbname=pipeline_test user=tester"
    finally:
        subprocess.run(
            [pg_ctl, "-D", str(data), "-w", "-m", "fast", "stop"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
