"""CSV source connector: file checks, schema checks, typed parsing and row-level rejection.

Every source row ends up either **accepted** (typed, normalized) or **rejected** with a reason code,
the file line number and the column concerned. Nothing is dropped silently.

Reason codes: ``malformed_row`` (wrong number of fields), ``missing_value``, ``invalid_number``,
``invalid_date``, ``invalid_value`` (not in the allowed list), ``out_of_range``, ``duplicate_row``
(exact copy of an earlier row with the same key: the first one is kept) and
``duplicate_key_conflict`` (same key, different content: every copy is rejected because the
pipeline cannot choose).
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .errors import SourceError

MAX_FILE_BYTES = 200 * 1024 * 1024
RAW = "_raw"  # key of the original text values in an accepted row
_MONEY = re.compile(r"^-?\d+(\.\d{1,2})?$")
_RATE = re.compile(r"^\d+(\.\d{1,4})?$")
_INTEGER = re.compile(r"^-?\d+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class RowError(ValueError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


# --------------------------------------------------------------------------- column parsers


def text(value: str) -> str:
    return value


def money_cents(value: str) -> int:
    """'1049.00' -> 104900. Point decimal separator, at most 2 decimals, no symbols."""
    if not _MONEY.match(value):
        raise RowError("invalid_number", f"{value!r} is not an amount like 1234.56")
    cents = int((Decimal(value) * 100).to_integral_value())
    if cents < 0:
        raise RowError("out_of_range", f"{value} is negative")
    return cents


def rate_basis_points(value: str) -> int:
    """'0.15' -> 1500 basis points; must be between 0 and 1."""
    if not _RATE.match(value):
        raise RowError("invalid_number", f"{value!r} is not a rate like 0.15")
    try:
        points = int((Decimal(value) * 10000).to_integral_value())
    except InvalidOperation:
        raise RowError("invalid_number", f"{value!r} is not a rate") from None
    if points > 10000:
        raise RowError("out_of_range", f"{value} is above 1")
    return points


def positive_int(maximum: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        if not _INTEGER.match(value):
            raise RowError("invalid_number", f"{value!r} is not a whole number")
        number = int(value)
        if not 1 <= number <= maximum:
            raise RowError("out_of_range", f"{number} is outside 1..{maximum}")
        return number

    return parse


def iso_date(value: str) -> str:
    if not _DATE.match(value):
        raise RowError("invalid_date", f"{value!r} is not a date in YYYY-MM-DD format")
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise RowError("invalid_date", f"{value!r} is not a real calendar date") from None


def one_of(*allowed: str) -> Callable[[str], str]:
    canonical = {a.casefold(): a for a in allowed}

    def parse(value: str) -> str:
        found = canonical.get(value.casefold())
        if found is None:
            raise RowError("invalid_value", f"{value!r} is not one of {', '.join(allowed)}")
        return found  # case is normalized: 'online' -> 'Online'

    return parse


# --------------------------------------------------------------------------- source schemas


@dataclass(frozen=True)
class Column:
    name: str
    parse: Callable[[str], Any]
    target: str | None = None  # column name in staging, if different

    @property
    def staging_name(self) -> str:
        return self.target or self.name


@dataclass(frozen=True)
class SourceSpec:
    table: str
    columns: tuple[Column, ...]
    key: tuple[str, ...]


SEGMENTS = ("Consumer", "Small Business", "Corporate")
REGIONS = ("North", "South", "East", "West")
CHANNELS = ("Online", "Store", "Partner")
STATUSES = ("completed", "cancelled")

SPECS: dict[str, SourceSpec] = {
    "products": SourceSpec(
        "products",
        (
            Column("product_id", text),
            Column("product_name", text),
            Column("category", text),
            Column("unit_cost", money_cents, "unit_cost_cents"),
            Column("list_price", money_cents, "list_price_cents"),
            Column("launch_date", iso_date),
        ),
        ("product_id",),
    ),
    "orders": SourceSpec(
        "orders",
        (
            Column("order_id", text),
            Column("order_date", iso_date),
            Column("customer_id", text),
            Column("customer_segment", one_of(*SEGMENTS)),
            Column("region", one_of(*REGIONS)),
            Column("sales_channel", one_of(*CHANNELS)),
            Column("order_status", one_of(*STATUSES)),
        ),
        ("order_id",),
    ),
    "order_lines": SourceSpec(
        "order_lines",
        (
            Column("order_id", text),
            Column("line_number", positive_int(1000)),
            Column("product_id", text),
            Column("quantity", positive_int(100_000)),
            Column("unit_price", money_cents, "unit_price_cents"),
            Column("discount_rate", rate_basis_points, "discount_bp"),
        ),
        ("order_id", "line_number"),
    ),
}


# --------------------------------------------------------------------------- reading


@dataclass
class Rejection:
    table: str
    line: int
    reason: str
    column: str | None
    message: str
    raw: dict[str, str]


@dataclass
class SourceResult:
    table: str
    path: Path
    sha256: str
    rows_read: int
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
    normalized_values: int = 0
    warnings: list[str] = field(default_factory=list)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_file(path: Path) -> None:
    """Existence, type and size of a source file (before reading it)."""
    if not path.exists():
        raise SourceError(f"source file not found: {path.name}")
    if not path.is_file():
        raise SourceError(f"source path is not a file: {path.name}")
    size = path.stat().st_size
    if size == 0:
        raise SourceError(f"{path.name} is empty")
    if size > MAX_FILE_BYTES:
        raise SourceError(f"{path.name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")


def read_source(spec: SourceSpec, path: Path, encoding: str = "utf-8", delimiter: str = ",") -> SourceResult:
    """Read one CSV file and split its rows into accepted and rejected rows."""
    check_file(path)
    result = SourceResult(spec.table, path, file_sha256(path), 0)
    codec = "utf-8-sig" if encoding.lower().replace("_", "-") in ("utf-8", "utf8") else encoding
    try:
        with path.open(encoding=codec, newline="") as handle:
            reader = csv.reader(handle, delimiter=delimiter, strict=True)
            try:
                header = [h.strip() for h in next(reader)]
            except StopIteration:
                raise SourceError(f"{path.name} has no header row") from None
            _check_header(spec, path, header, result)
            positions = {name: header.index(name) for name in (c.name for c in spec.columns)}
            for number, fields in enumerate(reader, start=2):
                if not any(f.strip() for f in fields):
                    result.warnings.append(f"{path.name} line {number}: blank line ignored")
                    continue
                result.rows_read += 1
                raw = dict(zip(header, fields, strict=False))
                if len(fields) != len(header):
                    result.rejected.append(
                        Rejection(
                            spec.table,
                            number,
                            "malformed_row",
                            None,
                            f"{len(fields)} field(s) instead of {len(header)}",
                            raw,
                        )
                    )
                    continue
                _parse_row(spec, number, fields, positions, raw, result)
    except UnicodeDecodeError as exc:
        raise SourceError(f"{path.name} is not valid {encoding} (byte {exc.start}); set sources.encoding") from None
    except csv.Error as exc:
        raise SourceError(f"{path.name} is not valid CSV: {exc}") from None
    _resolve_duplicates(spec, result)
    return result


def _check_header(spec: SourceSpec, path: Path, header: list[str], result: SourceResult) -> None:
    duplicated = sorted({h for h in header if header.count(h) > 1})
    if duplicated:
        raise SourceError(f"{path.name}: duplicate column name(s) {', '.join(duplicated)}")
    missing = [c.name for c in spec.columns if c.name not in header]
    if missing:
        raise SourceError(f"{path.name}: missing required column(s) {', '.join(missing)} (found: {', '.join(header)})")
    extra = [h for h in header if h not in {c.name for c in spec.columns}]
    if extra:
        result.warnings.append(f"{path.name}: column(s) {', '.join(extra)} are not used and were ignored")


def _parse_row(spec, number, fields, positions, raw, result) -> None:
    row: dict[str, Any] = {"source_line": number, RAW: raw}  # raw values kept for rejection reports
    for column in spec.columns:
        value = fields[positions[column.name]].strip()
        if value == "":
            result.rejected.append(
                Rejection(spec.table, number, "missing_value", column.name, f"{column.name} is empty", raw)
            )
            return
        try:
            parsed = column.parse(value)
        except RowError as exc:
            result.rejected.append(Rejection(spec.table, number, exc.reason, column.name, str(exc), raw))
            return
        if isinstance(parsed, str) and parsed != fields[positions[column.name]]:
            result.normalized_values += 1  # trimmed or case-normalized
        row[column.staging_name] = parsed
    result.accepted.append(row)


def _resolve_duplicates(spec: SourceSpec, result: SourceResult) -> None:
    key_names = [next(c.staging_name for c in spec.columns if c.name == k) for k in spec.key]
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for row in result.accepted:
        groups.setdefault(tuple(row[k] for k in key_names), []).append(row)
    keep: list[dict[str, Any]] = []
    for key, rows in groups.items():
        if len(rows) == 1:
            keep.append(rows[0])
            continue
        content = [{k: v for k, v in r.items() if k not in ("source_line", RAW)} for r in rows]
        label = ", ".join(f"{k}={v}" for k, v in zip(spec.key, key, strict=True))
        if all(c == content[0] for c in content):
            keep.append(rows[0])
            for duplicate in rows[1:]:
                result.rejected.append(
                    Rejection(
                        spec.table,
                        duplicate["source_line"],
                        "duplicate_row",
                        None,
                        f"exact copy of line {rows[0]['source_line']} ({label})",
                        _raw(duplicate),
                    )
                )
        else:
            lines = ", ".join(str(r["source_line"]) for r in rows)
            for duplicate in rows:
                result.rejected.append(
                    Rejection(
                        spec.table,
                        duplicate["source_line"],
                        "duplicate_key_conflict",
                        None,
                        f"{label} appears on lines {lines} with different values",
                        _raw(duplicate),
                    )
                )
    order = {id(r): i for i, r in enumerate(result.accepted)}
    result.accepted = sorted(keep, key=lambda r: order[id(r)])
    result.rejected.sort(key=lambda r: r.line)


def _raw(row: dict[str, Any]) -> dict[str, str]:
    return row[RAW]
