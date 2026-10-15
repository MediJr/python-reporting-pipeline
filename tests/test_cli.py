"""Command-line interface: commands, messages, exit codes, installed entry points."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import ROOT

from reporting_pipeline import __version__, demo_data
from reporting_pipeline.cli import main


def test_help_and_version(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0 and f"reporting-pipeline {__version__}" in capsys.readouterr().out
    with pytest.raises(SystemExit) as info:
        main(["--help"])
    out = capsys.readouterr().out
    assert info.value.code == 0 and "check" in out and "run" in out and "exit codes" in out
    with pytest.raises(SystemExit) as info:
        main(["run"])
    assert info.value.code == 2  # --config is required


def test_check_is_read_only(project, capsys):
    assert main(["check", "--config", str(project / "config/pipeline.toml")]) == 0
    out = capsys.readouterr().out
    assert "orders: 4159 row(s) read, 4153 accepted, 6 rejected" in out
    assert "order_lines: 9790 row(s) read, 9784 accepted, 6 rejected" in out
    assert "Result: SOURCES USABLE (nothing was written)" in out
    assert not (project / "output").exists() and not (project / "runs").exists()


def test_run_success_and_failure_exit_codes(project, capsys):
    config = str(project / "config/pipeline.toml")
    assert main(["run", "--config", config]) == 0
    out = capsys.readouterr().out
    assert "Result: SUCCESS_WITH_REJECTIONS (exit code 0" in out and "checks: 19 passed, 0 failed" in out
    (project / "data/sample/orders.csv").write_text("order_id\nSO-1\n", encoding="utf-8")
    assert main(["run", "--config", config]) == 1
    err = capsys.readouterr().err
    assert "missing required column(s) order_date" in err and "Result: FAILED (exit code 1" in err


def test_refusals_exit_3(project, capsys):
    assert main(["run", "--config", str(project / "missing.toml")]) == 3
    assert "configuration file not found" in capsys.readouterr().err
    (project / "data/sample/orders.csv").unlink()
    assert main(["check", "--config", str(project / "config/pipeline.toml")]) == 3
    assert "source file not found: orders.csv" in capsys.readouterr().err
    (project / "runs").mkdir()
    (project / "runs/.lock").write_text("x", encoding="utf-8")
    assert main(["run", "--config", str(project / "config/pipeline.toml")]) == 3


def test_generate_demo_data_is_deterministic_and_matches_the_committed_files(tmp_path, capsys):
    assert main(["generate-demo-data", "--output", str(tmp_path)]) == 0
    for name in ("orders.csv", "order_lines.csv", "products.csv"):
        assert (tmp_path / name).read_bytes() == (ROOT / "data/sample" / name).read_bytes(), name
    assert main(["generate-demo-data", "--output", str(tmp_path)]) == 1  # refuses to overwrite
    assert "use --overwrite" in capsys.readouterr().err
    assert main(["generate-demo-data", "--output", str(tmp_path), "--overwrite", "--seed", "1"]) == 0
    assert (tmp_path / "orders.csv").read_bytes() != (ROOT / "data/sample/orders.csv").read_bytes()
    assert len(demo_data.ANOMALIES) == 15


def test_installed_command_from_another_directory(project, tmp_path):
    exe = Path(sys.executable).with_name("reporting-pipeline.exe" if os.name == "nt" else "reporting-pipeline")
    if not exe.exists():
        pytest.skip("the reporting-pipeline launcher is not installed in this environment")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    completed = subprocess.run(
        [str(exe), "run", "--config", str(project / "config/pipeline.toml")],
        cwd=elsewhere,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr
    assert (project / "output/powerbi/fact_sales.csv").exists() and not (elsewhere / "output").exists()
    module = subprocess.run([sys.executable, "-m", "reporting_pipeline", "--version"], capture_output=True, text=True)
    assert module.returncode == 0 and __version__ in module.stdout
