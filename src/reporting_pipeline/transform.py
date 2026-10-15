"""Python transformations: calendar dimension and KPI derivation.

SQL does the set-based work (joins, aggregations, rankings); Python does what is clearer in a
general-purpose language and identical on every engine: generating the calendar and computing the
period comparisons with exact ``Decimal`` arithmetic.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

DIM_DATE_COLUMNS = (
    "date_key",
    "full_date",
    "year",
    "quarter",
    "month",
    "month_key",
    "month_start",
    "month_name",
    "year_month",
    "day_of_month",
    "iso_weekday",
    "is_weekend",
)


def date_dimension(first: dt.date, last: dt.date) -> list[tuple]:
    """One row per day, from the first day of ``first``'s month to the last day of ``last``'s month."""
    start = first.replace(day=1)
    end = last.replace(day=calendar.monthrange(last.year, last.month)[1])
    rows = []
    day = start
    while day <= end:
        rows.append(
            (
                day.year * 10000 + day.month * 100 + day.day,
                day.isoformat(),
                day.year,
                (day.month - 1) // 3 + 1,
                day.month,
                day.year * 100 + day.month,
                day.replace(day=1).isoformat(),
                calendar.month_name[day.month],
                f"{day.year}-{day.month:02d}",
                day.day,
                day.isoweekday(),
                1 if day.isoweekday() >= 6 else 0,
            )
        )
        day += dt.timedelta(days=1)
    return rows


def net_amount_cents(quantity: int, unit_price_cents: int, discount_bp: int) -> int:
    """Reference implementation of the line amount rule (used to reconcile the SQL result)."""
    gross = Decimal(quantity) * Decimal(unit_price_cents)
    return int((gross * (Decimal(10000 - discount_bp) / Decimal(10000))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def money(cents: int | None) -> str | None:
    return None if cents is None else str((Decimal(cents) / 100).quantize(Decimal("0.01")))


def ratio(numerator: int | None, denominator: int | None, places: int = 4) -> str | None:
    """numerator / denominator rounded half-up; None when it cannot be computed (no fake zero)."""
    if numerator is None or not denominator:
        return None
    quantum = Decimal(1).scaleb(-places)
    return str((Decimal(numerator) / Decimal(denominator)).quantize(quantum, rounding=ROUND_HALF_UP))


def divide_cents(cents: int, count: int) -> int | None:
    """Exact cents / count, rounded half-up to the cent (None when count is 0)."""
    if not count:
        return None
    return int((Decimal(cents) / Decimal(count)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def growth(current: int | None, previous: int | None) -> str | None:
    if current is None or previous is None or previous == 0:
        return None
    return ratio(current - previous, previous)


@dataclass(frozen=True)
class Kpi:
    kpi: str
    label: str
    value: str | None
    unit: str
    period: str
    definition: str


def derive_kpis(monthly: list[dict], totals: dict, top_category: tuple | None, top_product: tuple | None) -> list[Kpi]:
    """KPIs from the monthly mart and a few totals. A KPI whose inputs are missing gets value None."""
    if not monthly:
        return []
    first, last = monthly[0]["year_month"], monthly[-1]["year_month"]
    whole = f"{first} to {last}"
    latest = monthly[-1]
    net = sum(m["net_sales_cents"] for m in monthly)
    orders = sum(m["orders"] for m in monthly)
    cancelled = sum(m["cancelled_orders"] for m in monthly)
    margin = sum(m["gross_margin_cents"] for m in monthly)
    last12 = monthly[-12:]
    prior12 = monthly[-24:-12] if len(monthly) >= 24 else []
    last12_net = sum(m["net_sales_cents"] for m in last12)
    prior12_net = sum(m["net_sales_cents"] for m in prior12) if len(prior12) == 12 else None
    l12 = f"{last12[0]['year_month']} to {last}"
    kpis = [
        Kpi("net_sales", "Net sales", money(net), "EUR", whole, "Sum of net line amounts of completed orders"),
        Kpi("orders", "Orders", str(orders), "count", whole, "Completed orders with at least one valid line"),
        Kpi(
            "units", "Units sold", str(sum(m["units"] for m in monthly)), "count", whole, "Quantity of completed lines"
        ),
        Kpi(
            "active_customers",
            "Active customers",
            str(totals["active_customers"]),
            "count",
            whole,
            "Distinct customers with a completed order",
        ),
        Kpi(
            "average_order_value",
            "Average order value",
            money(divide_cents(net, orders)),
            "EUR",
            whole,
            "Net sales / orders",
        ),
        Kpi("gross_margin", "Gross margin", money(margin), "EUR", whole, "Net sales - quantity x catalog unit cost"),
        Kpi("gross_margin_rate", "Gross margin rate", ratio(margin, net), "ratio", whole, "Gross margin / net sales"),
        Kpi(
            "discount_rate",
            "Average discount",
            ratio(sum(m["discount_amount_cents"] for m in monthly), sum(m["gross_sales_cents"] for m in monthly)),
            "ratio",
            whole,
            "Discount amount / gross sales (completed orders)",
        ),
        Kpi(
            "cancellation_rate",
            "Cancellation rate",
            ratio(cancelled, orders + cancelled),
            "ratio",
            whole,
            "Cancelled orders / all orders with valid lines",
        ),
        Kpi(
            "latest_month_net_sales",
            "Net sales, latest month",
            money(latest["net_sales_cents"]),
            "EUR",
            last,
            "Net sales of the last month in the data",
        ),
        Kpi(
            "mom_growth",
            "Growth vs previous month",
            growth(latest["net_sales_cents"], latest["prev_month_net_sales_cents"]),
            "ratio",
            last,
            "(latest month - previous month) / previous month",
        ),
        Kpi(
            "yoy_growth",
            "Growth vs same month last year",
            growth(latest["net_sales_cents"], latest["prev_year_net_sales_cents"]),
            "ratio",
            last,
            "(latest month - same month one year earlier) / same month one year earlier",
        ),
        Kpi(
            "last_12_months_net_sales",
            "Net sales, last 12 months",
            money(last12_net),
            "EUR",
            l12,
            "Sum of the last 12 months",
        ),
        Kpi(
            "rolling_12_months_growth",
            "Growth, last 12 vs prior 12 months",
            growth(last12_net, prior12_net),
            "ratio",
            l12,
            "(last 12 months - the 12 months before) / the 12 months before",
        ),
    ]
    if top_category:
        kpis.append(
            Kpi(
                "top_category",
                "Top category",
                f"{top_category[0]} ({money(top_category[1])})",
                "text",
                whole,
                "Category with the highest net sales",
            )
        )
    if top_product:
        kpis.append(
            Kpi(
                "top_product",
                "Top product",
                f"{top_product[0]} ({money(top_product[1])})",
                "text",
                whole,
                "Product with the highest net sales",
            )
        )
    return kpis
