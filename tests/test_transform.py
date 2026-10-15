"""Python transformations: calendar, amount rule, ratios and KPI derivation (values computed by hand)."""

from __future__ import annotations

import datetime as dt

import pytest

from reporting_pipeline.transform import date_dimension, derive_kpis, divide_cents, growth, net_amount_cents, ratio


def test_calendar_covers_whole_months_including_leap_day():
    rows = date_dimension(dt.date(2024, 2, 10), dt.date(2024, 3, 3))
    assert rows[0][:2] == (20240201, "2024-02-01") and rows[-1][:2] == (20240331, "2024-03-31")
    assert len(rows) == 29 + 31  # February 2024 has 29 days
    feb29 = next(r for r in rows if r[0] == 20240229)
    # date_key, full_date, year, quarter, month, month_key, month_start, month_name, year_month, day, weekday, weekend
    assert feb29 == (20240229, "2024-02-29", 2024, 1, 2, 202402, "2024-02-01", "February", "2024-02", 29, 4, 0)
    assert next(r for r in rows if r[0] == 20240302)[-2:] == (6, 1)  # Saturday


@pytest.mark.parametrize(
    "quantity, price_cents, discount_bp, net",
    [
        (2, 2490, 0, 4980),  # 2 x 24.90
        (5, 4900, 1500, 20825),  # 245.00 x 0.85 = 208.25
        (3, 333, 1000, 899),  # 9.99 x 0.9 = 8.991 -> 8.99
        (1, 5, 1000, 5),  # 0.05 x 0.9 = 0.045 -> 0.05 (half-up)
        (1, 15, 5000, 8),  # 0.15 x 0.5 = 0.075 -> 0.08 (half-up, not banker's rounding)
    ],
)
def test_net_amount_rule(quantity, price_cents, discount_bp, net):
    assert net_amount_cents(quantity, price_cents, discount_bp) == net


def test_ratios_never_invent_a_value():
    assert ratio(1, 3) == "0.3333" and ratio(2, 3) == "0.6667"
    assert ratio(5, 0) is None and ratio(None, 3) is None
    assert growth(110, 100) == "0.1000" and growth(90, 100) == "-0.1000"
    assert growth(100, None) is None and growth(100, 0) is None
    assert divide_cents(1000, 3) == 333 and divide_cents(1001, 2) == 501 and divide_cents(10, 0) is None


def _month(ym, net, orders=10, cancelled=1, prev=None, py=None):
    return {
        "year_month": ym,
        "net_sales_cents": net,
        "orders": orders,
        "cancelled_orders": cancelled,
        "units": orders * 2,
        "gross_margin_cents": net // 2,
        "discount_amount_cents": 0,
        "gross_sales_cents": net,
        "prev_month_net_sales_cents": prev,
        "prev_year_net_sales_cents": py,
    }


def test_kpis_from_monthly_figures():
    monthly = [_month("2026-01", 100000), _month("2026-02", 120000, prev=100000)]
    kpis = {k.kpi: k for k in derive_kpis(monthly, {"active_customers": 7}, ("Technology", 150000), ("Lamp", 9900))}
    assert kpis["net_sales"].value == "2200.00" and kpis["orders"].value == "20"
    assert kpis["average_order_value"].value == "110.00"
    assert kpis["mom_growth"].value == "0.2000"
    assert kpis["yoy_growth"].value is None  # no data one year earlier: not computed, not zero
    assert kpis["rolling_12_months_growth"].value is None  # fewer than 24 months
    assert kpis["cancellation_rate"].value == "0.0909"  # 2 / 22
    assert kpis["top_category"].value == "Technology (1500.00)"
    assert derive_kpis([], {}, None, None) == []
