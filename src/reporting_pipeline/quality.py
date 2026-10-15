"""Quality gates and reconciliation checks, run inside the build transaction (before anything is committed).

Two kinds of checks:

* **gates** on the source data: the share of rejected rows per source must not exceed the configured
  threshold (``[quality] max_rejected_rate``);
* **reconciliations** of the model: row counts, keys, referential integrity and totals compared
  between staging, the fact table and every mart, plus an independent Python recomputation of net
  sales from the accepted source rows (it does not read the SQL results).

Any failed check stops the run: the transaction is rolled back and nothing is exported.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from .transform import net_amount_cents
from .warehouse import Warehouse


@dataclass
class Check:
    name: str
    category: str  # "gate", "reconciliation", "integrity" or "info"
    passed: bool
    expected: Any
    actual: Any
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def gate_checks(read: dict[str, int], rejected: Counter, thresholds: dict[str, float]) -> list[Check]:
    checks = []
    for table, rows_read in read.items():
        count = rejected.get(table, 0)
        rate = count / rows_read if rows_read else 0.0
        limit = thresholds.get(table, 0.0)
        checks.append(
            Check(
                f"rejected_rate_{table}",
                "gate",
                rate <= limit,
                f"<= {limit:.2%}",
                f"{rate:.2%}",
                f"{count} of {rows_read} {table} row(s) rejected",
            )
        )
    return checks


def model_checks(
    warehouse: Warehouse, accepted_lines: list[dict], accepted_orders: dict[str, dict], lines_rejected_by_integrity: int
) -> list[Check]:
    w = warehouse
    checks: list[Check] = []

    def add(name: str, category: str, expected: Any, actual: Any, detail: str) -> None:
        checks.append(Check(name, category, expected == actual, expected, actual, detail))

    fact_rows = w.scalar("SELECT COUNT(*) FROM fact_sales")
    add(
        "fact_rows_match_valid_lines",
        "reconciliation",
        len(accepted_lines) - lines_rejected_by_integrity,
        fact_rows,
        "accepted order lines minus lines rejected by referential integrity = fact rows (no row lost or multiplied)",
    )
    add(
        "fact_key_unique",
        "integrity",
        fact_rows,
        w.scalar("SELECT COUNT(*) FROM (SELECT DISTINCT order_id, line_number FROM fact_sales) k"),
        "one fact row per (order_id, line_number)",
    )
    add(
        "fact_products_in_dimension",
        "integrity",
        0,
        w.scalar(
            "SELECT COUNT(*) FROM fact_sales f WHERE NOT EXISTS "
            "(SELECT 1 FROM dim_product p WHERE p.product_id = f.product_id)"
        ),
        "fact rows whose product is missing from dim_product",
    )
    add(
        "fact_dates_in_dimension",
        "integrity",
        0,
        w.scalar(
            "SELECT COUNT(*) FROM fact_sales f WHERE NOT EXISTS "
            "(SELECT 1 FROM dim_date d WHERE d.date_key = f.date_key)"
        ),
        "fact rows whose date is missing from dim_date",
    )
    add(
        "fact_critical_nulls",
        "integrity",
        0,
        w.scalar(
            "SELECT COUNT(*) FROM fact_sales WHERE order_id IS NULL OR product_id IS NULL OR date_key IS NULL "
            "OR net_amount_cents IS NULL OR quantity IS NULL"
        ),
        "missing values in key or amount columns of the fact table",
    )
    days = w.scalar("SELECT COUNT(*) FROM dim_date")
    first, last = w.query("SELECT MIN(date_key), MAX(date_key) FROM dim_date")[1][0]
    add(
        "dim_date_continuous",
        "integrity",
        days,
        w.scalar("SELECT COUNT(DISTINCT full_date) FROM dim_date"),
        f"calendar from {first} to {last} without duplicate days",
    )

    # independent recomputation in Python, from the accepted source rows (not from SQL)
    present = {(r["order_id"], r["line_number"]) for r in _fact_keys(w)}
    python_net = sum(
        net_amount_cents(r["quantity"], r["unit_price_cents"], r["discount_bp"])
        for r in accepted_lines
        if (r["order_id"], r["line_number"]) in present
        and accepted_orders[r["order_id"]]["order_status"] == "completed"
    )
    fact_net = w.scalar("SELECT COALESCE(SUM(net_amount_cents), 0) FROM fact_sales WHERE is_completed = 1")
    add(
        "net_sales_python_vs_sql",
        "reconciliation",
        python_net,
        fact_net,
        "net sales recomputed in Python from the source rows = SQL fact total (cents)",
    )
    for mart in ("mart_monthly_sales", "mart_category_monthly", "mart_product_performance", "mart_segment_region"):
        add(
            f"{mart}_net_sales",
            "reconciliation",
            fact_net,
            w.scalar(f"SELECT COALESCE(SUM(net_sales_cents), 0) FROM {mart}"),  # noqa: S608 - fixed table names
            f"total net sales of {mart} = fact total (cents)",
        )
    completed_orders = w.scalar("SELECT COUNT(DISTINCT order_id) FROM fact_sales WHERE is_completed = 1")
    add(
        "monthly_orders",
        "reconciliation",
        completed_orders,
        w.scalar("SELECT SUM(orders) FROM mart_monthly_sales"),
        "sum of monthly orders = distinct completed orders (an order belongs to one month)",
    )
    add(
        "segment_orders",
        "reconciliation",
        completed_orders,
        w.scalar("SELECT SUM(orders) FROM mart_segment_region"),
        "sum of orders per segment and region = distinct completed orders",
    )
    add(
        "monthly_units",
        "reconciliation",
        w.scalar("SELECT COALESCE(SUM(quantity), 0) FROM fact_sales WHERE is_completed = 1"),
        w.scalar("SELECT SUM(units) FROM mart_monthly_sales"),
        "sum of monthly units = fact units",
    )
    shares = w.query(
        "SELECT month_key, SUM(share_of_month_bp), COUNT(*) FROM mart_category_monthly GROUP BY month_key"
    )[1]
    off = [month for month, total, categories in shares if abs(total - 10000) > categories]
    add(
        "category_shares_sum_to_100pct",
        "reconciliation",
        [],
        off,
        "category shares of each month add up to 100 % (within rounding of 1 bp per category)",
    )
    orders_without_lines = len(set(accepted_orders) - {k[0] for k in present})
    checks.append(
        Check(
            "orders_without_valid_lines",
            "info",
            True,
            "-",
            orders_without_lines,
            "accepted orders with no valid line: not in the fact table (they have no amount)",
        )
    )
    return checks


def _fact_keys(w: Warehouse) -> list[dict]:
    columns, rows = w.query("SELECT order_id, line_number FROM fact_sales")
    return [dict(zip(columns, r, strict=True)) for r in rows]
