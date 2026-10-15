"""Power BI exports: one CSV per model table, a manifest, and an atomic replacement of the folder.

Format (stable, documented in docs/data-model.md):

* UTF-8 without BOM, comma delimiter, LF line endings, one header row;
* ``*_cents`` columns are exported as decimal amounts without the suffix (``net_amount_cents`` 12345 ->
  ``net_amount`` 123.45); ``*_bp`` columns as ratios with 4 decimals without the suffix
  (``discount_rate_bp`` 1500 -> ``discount_rate`` 0.1500);
* dates in ISO 8601 (``YYYY-MM-DD``); missing values as empty fields;
* rows sorted by the table's key, so two runs on the same data give byte-identical files.

The files are written to a temporary sibling folder, read back and compared, then the folder is swapped
in; a failure leaves the previous export untouched.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import shutil
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .errors import PipelineError
from .warehouse import Warehouse

#: table -> ORDER BY clause (fixed, deterministic)
EXPORTS: dict[str, str] = {
    "dim_date": "date_key",
    "dim_product": "product_id",
    "fact_sales": "order_id, line_number",
    "mart_monthly_sales": "month_key",
    "mart_category_monthly": "month_key, category",
    "mart_product_performance": "sales_rank, product_id",
    "mart_segment_region": "customer_segment, region",
    "kpi_summary": "position",
    "rejected_rows": "source_table, source_line, reason",
}


class ExportError(PipelineError):
    """The Power BI files could not be written or verified; the previous export is kept."""


@dataclass
class ExportedFile:
    name: str
    rows: int
    columns: list[str]
    sha256: str


def _convert(column: str, value: Any) -> tuple[str, str]:
    if column.endswith("_cents"):
        name = column[: -len("_cents")]
        return name, "" if value is None else str((Decimal(value) / 100).quantize(Decimal("0.01")))
    if column.endswith("_bp"):
        name = column[: -len("_bp")]
        return name, "" if value is None else str((Decimal(value) / 10000).quantize(Decimal("0.0001")))
    if value is None:
        return column, ""
    if isinstance(value, dt.date):
        return column, value.isoformat()
    return column, str(value)


def render_table(warehouse: Warehouse, table: str) -> tuple[bytes, ExportedFile]:
    columns, rows = warehouse.query(f"SELECT * FROM {table} ORDER BY {EXPORTS[table]}")  # noqa: S608 - fixed names
    names = [_convert(c, None)[0] for c in columns]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(names)
    for row in rows:
        writer.writerow([_convert(c, v)[1] for c, v in zip(columns, row, strict=True)])
    data = buffer.getvalue().encode("utf-8")
    return data, ExportedFile(f"{table}.csv", len(rows), names, hashlib.sha256(data).hexdigest())


def export_powerbi(
    warehouse: Warehouse, target: Path, manifest: dict[str, Any], extra: dict[str, bytes]
) -> list[ExportedFile]:
    """Write every table plus ``extra`` files and ``manifest.json``; swap the folder in atomically."""
    run_id = manifest["run_id"]
    temp = target.with_name(f".{target.name}.tmp-{run_id[:8]}")
    old = target.with_name(f".{target.name}.old-{run_id[:8]}")
    shutil.rmtree(temp, ignore_errors=True)
    temp.mkdir(parents=True)
    files: list[ExportedFile] = []
    try:
        for table in EXPORTS:
            data, info = render_table(warehouse, table)
            (temp / info.name).write_bytes(data)
            files.append(info)
        for name, data in extra.items():
            (temp / name).write_bytes(data)
            files.append(ExportedFile(name, data.count(b"\n") - 1, [], hashlib.sha256(data).hexdigest()))
        manifest = dict(manifest, files=[f.__dict__ for f in files])
        (temp / "manifest.json").write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
        _verify(temp, files)
        if target.exists():
            target.rename(old)
        try:
            temp.rename(target)
        except OSError:
            if old.exists():
                old.rename(target)
            raise
        shutil.rmtree(old, ignore_errors=True)
    except OSError as exc:
        shutil.rmtree(temp, ignore_errors=True)
        raise ExportError(f"cannot write the Power BI export folder: {exc}") from None
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)  # verification failure or interruption: no leftover
        raise
    return files


def _verify(folder: Path, files: list[ExportedFile]) -> None:
    for info in files:
        data = (folder / info.name).read_bytes()
        if hashlib.sha256(data).hexdigest() != info.sha256:
            raise ExportError(f"{info.name}: content read back differs from what was written")
        if data.startswith(b"\xef\xbb\xbf") or b"\r\n" in data:
            raise ExportError(f"{info.name}: BOM or CRLF found")
        if info.columns:
            with io.StringIO(data.decode("utf-8")) as handle:
                rows = list(csv.reader(handle))
            if rows[0] != info.columns or len(rows) - 1 != info.rows:
                raise ExportError(f"{info.name}: header or row count read back does not match")
