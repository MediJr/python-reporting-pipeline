"""Warehouse connectors: SQLite (file, no server) and PostgreSQL (psycopg), behind one small interface.

Safety rules (both engines):

* the pipeline only works in a database (SQLite file) or schema (PostgreSQL) that it created itself:
  a marker row in ``rp_metadata`` identifies it. An existing database or schema without the marker is
  never modified;
* the model is rebuilt inside **one transaction**: if anything fails, the previous model stays as it
  was (both engines support transactional DDL);
* values from outside (source rows) are always passed as query parameters, never formatted into SQL;
  table and column names come from the pipeline's own fixed schema and are checked against a pattern;
* PostgreSQL credentials come from an environment variable and never appear in messages.
"""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3
from collections.abc import Iterable, Sequence
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any

from .config import Config
from .errors import WarehouseError

APPLICATION = "python-reporting-pipeline"
SQL_FILES = ("10_schema.sql", "20_integrity.sql", "30_model.sql", "40_marts.sql")
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_SECRET = re.compile(r"(password\s*=\s*)\S+|(://[^:/@\s]+:)[^@\s]+@", re.IGNORECASE)

METADATA_DDL = (
    "CREATE TABLE IF NOT EXISTS rp_metadata (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS rp_runs ("
    " run_id TEXT NOT NULL PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,"
    " status TEXT NOT NULL, fact_rows BIGINT, rejected_rows BIGINT, message TEXT)",
)


def load_sql(name: str, dialect: str) -> list[str]:
    """Statements of a packaged SQL file, with the dialect token replaced (``{{DATE}}``)."""
    text = resources.files("reporting_pipeline").joinpath("sql", name).read_text(encoding="utf-8")
    text = text.replace("{{DATE}}", "DATE" if dialect == "postgres" else "TEXT")
    return split_statements(text)


def split_statements(text: str) -> list[str]:
    """Split a SQL file on semicolons that end a line (the files never put ';' inside strings)."""
    statements, current = [], []
    for line in text.splitlines():
        stripped = line.strip()
        if not current and (not stripped or stripped.startswith("--")):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current).rstrip().rstrip(";"))
            current = []
    if any(line.strip() and not line.strip().startswith("--") for line in current):
        raise WarehouseError("SQL file ends without a semicolon")
    return statements


def redact(message: str) -> str:
    """Remove passwords from driver messages (they may echo connection parameters)."""
    return _SECRET.sub(lambda m: (m.group(1) or m.group(2)) + "***" + ("@" if m.group(2) else ""), message)


def _execute(cursor, sql: str, params: Sequence[Any]) -> None:
    """Without parameters, call execute(sql) alone: psycopg would otherwise read '%' (modulo) as a placeholder."""
    if params:
        cursor.execute(sql, tuple(params))
    else:
        cursor.execute(sql)


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    return value


class Warehouse:
    dialect: str
    placeholder: str
    description: str

    def begin(self) -> None:
        raise NotImplementedError

    def commit(self) -> None:
        raise NotImplementedError

    def rollback(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def _cursor(self):
        raise NotImplementedError

    def _owned_state(self) -> str:
        """'new' (nothing there yet), 'owned' (marker present) or 'foreign'."""
        raise NotImplementedError

    # ------------------------------------------------------------------ shared logic

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        try:
            _execute(self._cursor(), sql, params)
        except WarehouseError:
            raise
        except Exception as exc:
            raise WarehouseError(f"SQL error: {redact(str(exc)).strip()}") from None

    def query(self, sql: str, params: Sequence[Any] = ()) -> tuple[list[str], list[tuple]]:
        try:
            cursor = self._cursor()
            _execute(cursor, sql, params)
            columns = [d[0] for d in cursor.description]
            return columns, [tuple(_plain(v) for v in row) for row in cursor.fetchall()]
        except Exception as exc:
            raise WarehouseError(f"SQL error: {redact(str(exc)).strip()}") from None

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        _, rows = self.query(sql, params)
        return rows[0][0] if rows else None

    def run_file(self, name: str) -> None:
        for statement in load_sql(name, self.dialect):
            self.execute(statement)

    def insert_rows(self, table: str, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> int:
        for identifier in (table, *columns):
            if not _IDENT.match(identifier):
                raise WarehouseError(f"invalid identifier {identifier!r}")
        marks = ", ".join([self.placeholder] * len(columns))
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks})"  # noqa: S608 - fixed identifiers
        data = [tuple(r) for r in rows]
        if not data:
            return 0
        try:
            self._cursor().executemany(sql, data)
        except Exception as exc:
            raise WarehouseError(f"cannot insert into {table}: {redact(str(exc)).strip()}") from None
        return len(data)

    def prepare(self) -> None:
        """Check that the target is a pipeline database (or empty), then create the metadata tables."""
        state = self._owned_state()
        if state == "foreign":
            raise WarehouseError(
                f"{self.description} already exists and was not created by this pipeline "
                "(no rp_metadata marker); refusing to modify it. Choose another target."
            )
        self.begin()
        for statement in METADATA_DDL:
            self.execute(statement)
        if state == "new":
            self.execute(
                f"INSERT INTO rp_metadata (key, value) VALUES ({self.placeholder}, {self.placeholder})",  # noqa: S608
                ("application", APPLICATION),
            )
        self.commit()


class SqliteWarehouse(Warehouse):
    dialect = "sqlite"
    placeholder = "?"

    def __init__(self, path: Path) -> None:
        self.path = path
        self.description = f"SQLite database {path.name}"
        existed = path.exists()
        if existed and not path.is_file():
            raise WarehouseError(f"{path.name} is not a file")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.connection = sqlite3.connect(path, isolation_level=None)  # explicit BEGIN/COMMIT below
            self.connection.execute("PRAGMA foreign_keys = ON")
            if existed:
                self.connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        except sqlite3.DatabaseError as exc:
            raise WarehouseError(f"{path.name} is not a usable SQLite database: {exc}") from None
        self._cur = self.connection.cursor()

    def _cursor(self):
        return self._cur

    def begin(self) -> None:
        self.connection.execute("BEGIN")

    def commit(self) -> None:
        self.connection.execute("COMMIT")

    def rollback(self) -> None:
        if self.connection.in_transaction:
            self.connection.execute("ROLLBACK")

    def close(self) -> None:
        self.connection.close()

    def _owned_state(self) -> str:
        tables = {r[0] for r in self.connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not tables:
            return "new"
        if "rp_metadata" in tables:
            row = self.connection.execute("SELECT value FROM rp_metadata WHERE key = 'application'").fetchone()
            if row and row[0] == APPLICATION:
                return "owned"
        return "foreign"


class PostgresWarehouse(Warehouse):
    dialect = "postgres"
    placeholder = "%s"

    def __init__(self, dsn: str, schema: str, env_name: str) -> None:
        try:
            import psycopg
        except ImportError:
            raise WarehouseError(
                "PostgreSQL support needs psycopg: pip install 'python-reporting-pipeline[postgres]'"
            ) from None
        if not _IDENT.match(schema):
            raise WarehouseError(f"invalid schema name {schema!r}")
        self.schema = schema
        self.description = f"PostgreSQL schema {schema!r}"
        try:
            self.connection = psycopg.connect(dsn, autocommit=True, connect_timeout=10)
        except Exception as exc:
            raise WarehouseError(
                f"cannot connect to PostgreSQL (connection string from ${env_name}): {redact(str(exc)).strip()}"
            ) from None
        self._cur = self.connection.cursor()
        self._cur.execute(f"SET search_path TO {self.schema}")  # session level; schema name validated above

    def _cursor(self):
        return self._cur

    def begin(self) -> None:
        self._cur.execute("BEGIN")

    def commit(self) -> None:
        self._cur.execute("COMMIT")

    def rollback(self) -> None:
        with contextlib.suppress(Exception):  # nothing to roll back
            self._cur.execute("ROLLBACK")

    def close(self) -> None:
        self.connection.close()

    def _owned_state(self) -> str:
        exists = self.scalar("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = %s", (self.schema,))
        if not exists:
            self.execute(f"CREATE SCHEMA {self.schema}")
            return "new"
        tables = self.scalar("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s", (self.schema,))
        if not tables:
            return "new"
        marker = self.scalar(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = 'rp_metadata'",
            (self.schema,),
        )
        if marker:
            value = self.scalar(f"SELECT value FROM {self.schema}.rp_metadata WHERE key = 'application'")  # noqa: S608
            if value == APPLICATION:
                return "owned"
        return "foreign"


def open_warehouse(config: Config) -> Warehouse:
    if config.engine == "sqlite":
        return SqliteWarehouse(config.sqlite_path)
    dsn = os.environ.get(config.connection_env or "")
    if not dsn:
        raise WarehouseError(
            f"environment variable {config.connection_env} is not set (it must hold the PostgreSQL connection string)"
        )
    return PostgresWarehouse(dsn, config.schema, config.connection_env)
