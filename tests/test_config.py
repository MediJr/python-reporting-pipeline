"""Configuration loading: strict keys, paths confined to the project root, no credentials in the file."""

from __future__ import annotations

import pytest
from conftest import write_config

from reporting_pipeline.config import load_config
from reporting_pipeline.errors import ConfigError


def test_valid_configuration(project):
    config = load_config(project / "config" / "pipeline.toml")
    assert config.root == project.resolve()
    assert config.source_files["orders"] == (project / "data/sample/orders.csv").resolve()
    assert config.engine == "sqlite" and config.sqlite_path.name == "warehouse.sqlite"
    assert config.max_rejected_rate == {"orders": 0.01, "order_lines": 0.01, "products": 0.05}
    assert config.relative(config.powerbi_dir) == "output/powerbi"


def test_committed_demo_configurations_are_valid():
    from conftest import ROOT

    assert load_config(ROOT / "config" / "pipeline.toml").engine == "sqlite"
    pg = load_config(ROOT / "config" / "pipeline.postgres.example.toml")
    assert (pg.engine, pg.connection_env, pg.schema) == ("postgres", "REPORTING_PG_DSN", "reporting")


@pytest.mark.parametrize(
    "old, new, message",
    [
        ('engine = "sqlite"', 'engine = "mysql"', "warehouse.engine must be one of"),
        (
            'powerbi_dir = "output/powerbi"',
            'powerbi_dir = "output/powerbi"\ncolour = "red"',
            r"unknown key\(s\) in \[outputs\]: colour",
        ),
        ("[quality]", "[extras]\na = 1\n[quality]", r"unknown section\(s\): extras"),
        ('directory = "data/sample"', 'directory = "../outside"', "leaves the project root"),
        ('directory = "data/sample"', 'directory = "C:/data"', "must be relative"),
        ('powerbi_dir = "output/powerbi"', 'powerbi_dir = "data/sample/out"', "dedicated folder"),
        ('runs_dir = "runs"', 'runs_dir = "."', "dedicated folder"),
        ('orders = "orders.csv"', 'orders = "orders.txt"', "must be a .csv file"),
        ('orders = "orders.csv"', 'orders = "../../x.csv"', "leaves sources.directory"),
        ("orders = 0.01,", "orders = 2,", "between 0 and 1"),
        ('products = "products.csv"', "", "must name exactly"),
    ],
)
def test_invalid_configurations_are_rejected_with_a_precise_message(project, old, new, message):
    path = project / "config" / "pipeline.toml"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_postgres_requires_an_environment_variable_name_not_a_connection_string(project):
    bad = write_config(
        project,
        engine="postgres",
        warehouse='connection_env = "host=db password=secret"\nschema = "reporting"',
        name="pg.toml",
    )
    with pytest.raises(ConfigError, match="NAME of an environment variable") as info:
        load_config(bad)
    assert "secret" not in str(info.value)
    with pytest.raises(ConfigError, match="lower-case identifier"):
        load_config(
            write_config(
                project, engine="postgres", warehouse='connection_env = "X"\nschema = "Bad; DROP"', name="pg2.toml"
            )
        )


def test_missing_or_malformed_files(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")
    broken = tmp_path / "broken.toml"
    broken.write_text("[project\nname = 1", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(broken)
    empty = tmp_path / "empty.toml"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"missing section \[project\]"):
        load_config(empty)
