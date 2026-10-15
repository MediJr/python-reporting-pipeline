"""Deterministic generator of the synthetic demo data set (monthly sales of an office-equipment retailer).

The same seed always produces the same bytes. 24 months of orders (July 2024 to June 2026) with a
growth trend, seasonality (year-end peak, summer dip), category mix changes over time and a small
set of **deliberately invalid rows** (see ``ANOMALIES``) used to demonstrate and test the quality
checks. All names and identifiers are invented.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import random
from dataclasses import dataclass
from pathlib import Path

SEED = 20260701
FIRST_MONTH = dt.date(2024, 7, 1)
MONTHS = 24

#: product_id, product_name, category, unit_cost, list_price, launch_date
PRODUCTS: list[tuple[str, str, str, str, str, str]] = [
    ("P001", "Laptop 14in", "Technology", "520.00", "799.00", "2023-01-15"),
    ("P002", "Laptop 16in", "Technology", "690.00", "1049.00", "2023-01-15"),
    ("P003", "Monitor 24in", "Technology", "105.00", "169.00", "2023-03-01"),
    ("P004", "Monitor 27in", "Technology", "165.00", "259.00", "2024-10-01"),
    ("P005", "Wireless Mouse", "Technology", "9.50", "24.90", "2022-06-01"),
    ("P006", "Mechanical Keyboard", "Technology", "38.00", "79.00", "2022-06-01"),
    ("P007", "Noise-Cancelling Headset", "Technology", "62.00", "129.00", "2025-03-01"),
    ("P008", "HD Webcam", "Technology", "24.00", "59.00", "2022-06-01"),
    ("P009", "Docking Station", "Technology", "88.00", "149.00", "2023-09-01"),
    ("P010", "Ergonomic Office Chair", "Furniture", "145.00", "289.00", "2022-01-10"),
    ("P011", "Standing Desk", "Furniture", "260.00", "449.00", "2023-02-01"),
    ("P012", "Bookshelf", "Furniture", "70.00", "139.00", "2022-01-10"),
    ("P013", "Filing Cabinet", "Furniture", "85.00", "159.00", "2022-01-10"),
    ("P014", "LED Desk Lamp", "Furniture", "14.00", "34.90", "2022-01-10"),
    ("P015", "Meeting Table", "Furniture", "310.00", "599.00", "2023-05-01"),
    ("P016", "Copy Paper Ream", "Office Supplies", "2.60", "5.49", "2021-01-01"),
    ("P017", "Notebook Pack", "Office Supplies", "4.10", "9.90", "2021-01-01"),
    ("P018", "Gel Pen Set", "Office Supplies", "3.20", "7.90", "2021-01-01"),
    ("P019", "Heavy-Duty Stapler", "Office Supplies", "8.40", "19.90", "2021-01-01"),
    ("P020", "Binder Pack", "Office Supplies", "5.30", "12.90", "2021-01-01"),
    ("P021", "Sticky Notes Bundle", "Office Supplies", "2.10", "5.90", "2021-01-01"),
    ("P022", "Printer Toner", "Office Supplies", "34.00", "69.00", "2021-01-01"),
    ("P023", "Laptop Backpack", "Accessories", "21.00", "49.00", "2022-03-01"),
    ("P024", "Laptop Sleeve", "Accessories", "8.00", "22.90", "2022-03-01"),
    ("P025", "Cable Organizer Kit", "Accessories", "4.50", "14.90", "2022-03-01"),
    ("P026", "Phone Stand", "Accessories", "3.80", "12.90", "2022-03-01"),
    ("P027", "USB-C Hub", "Accessories", "15.00", "39.00", "2023-06-01"),
    ("P028", "Screen Cleaning Kit", "Accessories", "1.90", "7.50", "2022-03-01"),
]

SEASONALITY = {
    1: 0.85,
    2: 0.90,
    3: 1.00,
    4: 1.00,
    5: 1.05,
    6: 0.95,
    7: 0.85,
    8: 0.80,
    9: 1.05,
    10: 1.10,
    11: 1.30,
    12: 1.45,
}
SEGMENTS = [("Consumer", 50), ("Small Business", 30), ("Corporate", 20)]
REGIONS = [("North", 30), ("South", 25), ("East", 25), ("West", 20)]
CHANNELS = [("Online", 55), ("Store", 30), ("Partner", 15)]
CATEGORY_WEIGHT = {"Technology": 30, "Furniture": 18, "Office Supplies": 34, "Accessories": 18}
DISCOUNTS = [("0.00", 60), ("0.05", 20), ("0.10", 14), ("0.15", 6)]

ORDER_COLUMNS = ["order_id", "order_date", "customer_id", "customer_segment", "region", "sales_channel", "order_status"]
LINE_COLUMNS = ["order_id", "line_number", "product_id", "quantity", "unit_price", "discount_rate"]
PRODUCT_COLUMNS = ["product_id", "product_name", "category", "unit_cost", "list_price", "launch_date"]

#: Every anomaly injected on purpose, with the rule that must catch it (docs/data-quality.md).
ANOMALIES = [
    ("orders", "exact duplicate of an order row", "duplicate_row"),
    ("orders", "two rows with the same order_id and different regions", "duplicate_key_conflict"),
    ("orders", "order_date 2025-02-30 (not a real date)", "invalid_date"),
    ("orders", "customer_segment empty", "missing_value"),
    ("orders", "order_status 'shipped?' (not an allowed value)", "invalid_value"),
    ("orders", "sales_channel ' online' and customer_segment 'small business'", "normalized (kept)"),
    ("order_lines", "product_id empty", "missing_value"),
    ("order_lines", "product_id P999 not in the product catalog", "unknown_product"),
    ("order_lines", "quantity 0", "out_of_range"),
    ("order_lines", "quantity 'two'", "invalid_number"),
    ("order_lines", "unit_price '12,50' (decimal comma)", "invalid_number"),
    ("order_lines", "discount_rate 1.50 (above 1)", "out_of_range"),
    ("order_lines", "exact duplicate of a line row", "duplicate_row"),
    ("order_lines", "line of order SO-999999, which does not exist", "unknown_order"),
    ("products", "exact duplicate of a product row", "duplicate_row"),
]


@dataclass
class DemoData:
    orders: list[list[str]]
    lines: list[list[str]]
    products: list[list[str]]


def _pick(rng: random.Random, weighted: list[tuple[str, int]]) -> str:
    return rng.choices([v for v, _ in weighted], weights=[w for _, w in weighted])[0]


def _add_months(day: dt.date, months: int) -> dt.date:
    year, month = divmod(day.month - 1 + months, 12)
    return dt.date(day.year + year, month + 1, 1)


def generate(seed: int = SEED) -> DemoData:
    rng = random.Random(seed)  # noqa: S311 - reproducible synthetic data, not security
    products_by_category: dict[str, list[tuple[str, ...]]] = {}
    for product in PRODUCTS:
        products_by_category.setdefault(product[2], []).append(product)
    customers = {
        segment: [f"C{n:05d}" for n in range(start, start + size)]
        for segment, start, size in (("Consumer", 1, 600), ("Small Business", 601, 250), ("Corporate", 851, 90))
    }
    orders: list[list[str]] = []
    lines: list[list[str]] = []
    order_number = 0
    for m in range(MONTHS):
        month_start = _add_months(FIRST_MONTH, m)
        next_month = _add_months(FIRST_MONTH, m + 1)
        days = (next_month - month_start).days
        count = round(140 * (1 + 0.018 * m) * SEASONALITY[month_start.month])
        tech_boost = 1 + 0.025 * m  # Technology gains share over time
        weights = dict(CATEGORY_WEIGHT, Technology=CATEGORY_WEIGHT["Technology"] * tech_boost)
        for _ in range(count):
            order_number += 1
            order_id = f"SO-{order_number:06d}"
            day = month_start + dt.timedelta(days=rng.randrange(days))
            segment = _pick(rng, SEGMENTS)
            status = "cancelled" if rng.random() < 0.06 else "completed"
            orders.append(
                [
                    order_id,
                    day.isoformat(),
                    rng.choice(customers[segment]),
                    segment,
                    _pick(rng, REGIONS),
                    _pick(rng, CHANNELS),
                    status,
                ]
            )
            n_lines = rng.choice([1, 1, 2, 2, 3, 4] if segment != "Corporate" else [2, 3, 4, 5])
            chosen: set[str] = set()
            for _ in range(n_lines):
                category = rng.choices(list(weights), weights=list(weights.values()))[0]
                available = [p for p in products_by_category[category] if p[5] <= day.isoformat()]
                product = rng.choice(available)
                if product[0] in chosen:
                    continue
                chosen.add(product[0])
                if category == "Office Supplies":
                    quantity = rng.randint(2, 12)
                elif category == "Accessories":
                    quantity = rng.randint(1, 5)
                else:
                    quantity = rng.randint(1, 3)
                if segment == "Corporate":
                    quantity *= rng.choice([2, 3, 4])
                discount = _pick(rng, DISCOUNTS)
                if segment == "Corporate" and discount == "0.00" and rng.random() < 0.5:
                    discount = "0.10"
                lines.append([order_id, str(len(chosen)), product[0], str(quantity), product[4], discount])
    data = DemoData(orders, lines, [list(p) for p in PRODUCTS])
    _inject_anomalies(data)
    return data


def _inject_anomalies(data: DemoData) -> None:
    """Plant the rows listed in ``ANOMALIES`` at fixed positions (deterministic)."""
    orders, lines = data.orders, data.lines
    # orders
    orders.insert(101, list(orders[100]))  # exact duplicate row
    conflict = list(orders[401])
    conflict[4] = "South" if conflict[4] != "South" else "North"
    orders.insert(402, conflict)  # same order_id, different region
    orders[1001][1] = "2025-02-30"
    orders[1501][3] = ""
    orders[2001][6] = "shipped?"
    orders[2501][5] = " online"  # fixed by normalization (trimmed, canonical case), not rejected
    orders[3001][3] = "small business"
    # lines (positions chosen among orders that are otherwise valid)
    lines[300][2] = ""
    lines[600][2] = "P999"
    lines[900][3] = "0"
    lines[1200][3] = "two"
    lines[1500][4] = "12,50"
    lines[1800][5] = "1.50"
    lines.insert(2101, list(lines[2100]))  # exact duplicate row
    lines.append(["SO-999999", "1", "P005", "2", "24.90", "0.00"])
    # products
    data.products.append(list(data.products[4]))


def _csv_text(header: list[str], rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def write(directory: Path, data: DemoData | None = None, *, overwrite: bool = False) -> list[Path]:
    """Write orders.csv, order_lines.csv and products.csv (UTF-8, LF). Refuses to replace files unless asked."""
    data = data or generate()
    files = {
        "orders.csv": _csv_text(ORDER_COLUMNS, data.orders),
        "order_lines.csv": _csv_text(LINE_COLUMNS, data.lines),
        "products.csv": _csv_text(PRODUCT_COLUMNS, data.products),
    }
    existing = [directory / name for name in files if (directory / name).exists()]
    if existing and not overwrite:
        names = ", ".join(p.name for p in existing)
        raise FileExistsError(f"{names} already exist in {directory}; use --overwrite to replace them")
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in files.items():
        path = directory / name
        path.write_bytes(text.encode("utf-8"))
        written.append(path)
    return written
