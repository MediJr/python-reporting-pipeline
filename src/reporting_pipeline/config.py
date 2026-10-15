"""Pipeline configuration: a TOML file validated strictly before anything runs.

Rules:

* unknown sections or keys are errors (a typo never silently disables a setting);
* every path is relative to the project root (``[project] root``, itself relative to the folder of
  the configuration file) and must stay inside it, so a configuration cannot read or write elsewhere;
* database credentials are **never** written in the file: for PostgreSQL the file names an
  environment variable that holds the connection string.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError

SOURCE_TABLES = ("orders", "order_lines", "products")
ENGINES = ("sqlite", "postgres")
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")

_ALLOWED: dict[str, set[str]] = {
    "project": {"name", "root"},
    "sources": {"connector", "directory", "encoding", "delimiter", "files"},
    "warehouse": {"engine", "sqlite_path", "connection_env", "schema"},
    "outputs": {"powerbi_dir", "runs_dir"},
    "quality": {"max_rejected_rate"},
}


@dataclass(frozen=True)
class Config:
    path: Path
    name: str
    root: Path
    source_dir: Path
    source_files: dict[str, Path]
    encoding: str
    delimiter: str
    engine: str
    sqlite_path: Path | None
    connection_env: str | None
    schema: str
    powerbi_dir: Path
    runs_dir: Path
    max_rejected_rate: dict[str, float] = field(default_factory=dict)

    def relative(self, path: Path) -> str:
        """Path relative to the project root, with forward slashes (for reports and logs)."""
        return Path(os.path.relpath(path, self.root)).as_posix()


def _section(data: dict[str, Any], name: str, required: bool = True) -> dict[str, Any]:
    value = data.get(name)
    if value is None:
        if required:
            raise ConfigError(f"missing section [{name}]")
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    unknown = sorted(set(value) - _ALLOWED[name])
    if unknown:
        raise ConfigError(
            f"unknown key(s) in [{name}]: {', '.join(unknown)} (allowed: {', '.join(sorted(_ALLOWED[name]))})"
        )
    return value


def _text(section: dict[str, Any], where: str, key: str, default: str | None = None) -> str:
    value = section.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}.{key} must be a non-empty string")
    return value


def _inside(root: Path, relative: str, where: str) -> Path:
    if Path(relative).is_absolute() or re.match(r"^[A-Za-z]:", relative) or relative.startswith(("\\", "/")):
        raise ConfigError(f"{where} must be relative to the project root, not absolute: {relative!r}")
    resolved = (root / relative).resolve()
    if resolved != root and root not in resolved.parents:
        raise ConfigError(f"{where} leaves the project root: {relative!r}")
    return resolved


def load_config(path: str | Path) -> Config:
    """Read and validate a configuration file. Raises :class:`ConfigError` with a precise message."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"configuration file not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path.name} is not valid TOML: {exc}") from None
    unknown = sorted(set(data) - set(_ALLOWED))
    if unknown:
        raise ConfigError(f"unknown section(s): {', '.join(unknown)} (allowed: {', '.join(sorted(_ALLOWED))})")

    project = _section(data, "project")
    name = _text(project, "project", "name")
    root_text = _text(project, "project", "root", ".")
    root = (path.parent / root_text).resolve()
    if not root.is_dir():
        raise ConfigError(f"project.root does not exist: {root_text!r}")

    sources = _section(data, "sources")
    if sources.get("connector", "csv") != "csv":
        raise ConfigError(f"sources.connector must be 'csv' (got {sources.get('connector')!r})")
    source_dir = _inside(root, _text(sources, "sources", "directory"), "sources.directory")
    encoding = _text(sources, "sources", "encoding", "utf-8")
    try:
        "".encode(encoding)
    except LookupError:
        raise ConfigError(f"sources.encoding: unknown encoding {encoding!r}") from None
    delimiter = _text(sources, "sources", "delimiter", ",")
    if len(delimiter) != 1:
        raise ConfigError("sources.delimiter must be one character")
    files = sources.get("files")
    if not isinstance(files, dict):
        raise ConfigError("missing table [sources.files] (orders, order_lines, products)")
    if set(files) != set(SOURCE_TABLES):
        raise ConfigError(
            f"[sources.files] must name exactly: {', '.join(SOURCE_TABLES)} (got: {', '.join(sorted(files))})"
        )
    source_files = {}
    for table in SOURCE_TABLES:
        relative = files[table]
        if not isinstance(relative, str) or not relative.lower().endswith(".csv"):
            raise ConfigError(f"sources.files.{table} must be a .csv file name")
        target = (source_dir / relative).resolve()
        if source_dir not in target.parents:
            raise ConfigError(f"sources.files.{table} leaves sources.directory: {relative!r}")
        source_files[table] = target

    warehouse = _section(data, "warehouse")
    engine = _text(warehouse, "warehouse", "engine")
    if engine not in ENGINES:
        raise ConfigError(f"warehouse.engine must be one of {', '.join(ENGINES)} (got {engine!r})")
    sqlite_path = connection_env = None
    if engine == "sqlite":
        sqlite_path = _inside(root, _text(warehouse, "warehouse", "sqlite_path"), "warehouse.sqlite_path")
    else:
        connection_env = _text(warehouse, "warehouse", "connection_env")
        if not _ENV_NAME.match(connection_env):
            raise ConfigError(
                "warehouse.connection_env must be the NAME of an environment variable (e.g. REPORTING_PG_DSN), "
                "never the connection string itself"
            )
    schema = _text(warehouse, "warehouse", "schema", "reporting")
    if not _IDENTIFIER.match(schema):
        raise ConfigError(f"warehouse.schema must be a lower-case identifier (got {schema!r})")

    outputs = _section(data, "outputs")
    powerbi_dir = _inside(root, _text(outputs, "outputs", "powerbi_dir"), "outputs.powerbi_dir")
    runs_dir = _inside(root, _text(outputs, "outputs", "runs_dir"), "outputs.runs_dir")
    for label, target in (("outputs.powerbi_dir", powerbi_dir), ("outputs.runs_dir", runs_dir)):
        if target in (root, source_dir) or source_dir in target.parents or target in source_dir.parents:
            raise ConfigError(f"{label} must be a dedicated folder, separate from the project root and the sources")

    quality = _section(data, "quality", required=False)
    rates = quality.get("max_rejected_rate", {})
    if not isinstance(rates, dict) or set(rates) - set(SOURCE_TABLES):
        raise ConfigError(f"quality.max_rejected_rate must be a table with keys among {', '.join(SOURCE_TABLES)}")
    max_rates = {}
    for table in SOURCE_TABLES:
        value = rates.get(table, 0.0)
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
            raise ConfigError(f"quality.max_rejected_rate.{table} must be a number between 0 and 1")
        max_rates[table] = float(value)

    return Config(
        path=path.resolve(),
        name=name,
        root=root,
        source_dir=source_dir,
        source_files=source_files,
        encoding=encoding,
        delimiter=delimiter,
        engine=engine,
        sqlite_path=sqlite_path,
        connection_env=connection_env,
        schema=schema,
        powerbi_dir=powerbi_dir,
        runs_dir=runs_dir,
        max_rejected_rate=max_rates,
    )
