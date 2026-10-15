"""Generate the Power BI project (PBIP: TMDL semantic model + PBIR report) from one specification.

    python scripts/build_powerbi_project.py                      # committed version (placeholder data folder)
    python scripts/build_powerbi_project.py --data-folder "D:/work/python-reporting-pipeline/output/powerbi" \
        --output output/powerbi-project                          # local copy pointing at your exports

Power BI cannot use relative paths in Power Query, so the data folder is a model parameter
(``DataFolder``). The committed project uses a clearly fake placeholder; either regenerate a local
copy with ``--data-folder`` or change the parameter in Power BI Desktop (Transform data > Edit parameters).

Everything is generated from the tables, measures and pages declared below, so the model, the report
and ``powerbi/measures.dax`` cannot drift apart.
"""

from __future__ import annotations

import argparse
import json
import shutil
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "SalesPerformance"
PLACEHOLDER = r"C:\CHANGE-ME\python-reporting-pipeline\output\powerbi"
NAMESPACE = uuid.UUID("7d4c1f2e-0b7a-4d38-9a51-3c2f6e5b9d10")  # stable ids: same input, same files

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
VERSIONS = {"visual": "2.12.0", "page": "2.1.0", "report": "3.3.0"}
BASE_THEME = "Fluent2-CY26SU08"
EUR = "€#,0;-€#,0;€#,0"
EUR2 = "€#,0.00;-€#,0.00;€#,0.00"
PCT = "0.0%;-0.0%;0.0%"
COUNT = "#,0"

# --------------------------------------------------------------------------- semantic model

TEXT, INT, DEC, CUR, DATE = "string", "int64", "double", "decimal", "dateTime"
M_TYPE = {TEXT: "type text", INT: "Int64.Type", DEC: "type number", CUR: "Currency.Type", DATE: "type date"}

#: table name -> (csv file, [(column, type, extra properties)])
TABLES: dict[str, tuple[str, list[tuple[str, str, dict]]]] = {
    "Sales": (
        "fact_sales.csv",
        [
            ("order_id", TEXT, {}),
            ("line_number", INT, {"summarizeBy": "none"}),
            ("date_key", INT, {"summarizeBy": "none", "isHidden": True}),
            ("order_date", DATE, {"formatString": "yyyy-mm-dd"}),
            ("product_id", TEXT, {"isHidden": True}),
            ("customer_id", TEXT, {}),
            ("customer_segment", TEXT, {}),
            ("region", TEXT, {}),
            ("sales_channel", TEXT, {}),
            ("order_status", TEXT, {}),
            ("is_completed", INT, {"summarizeBy": "none", "isHidden": True}),
            ("quantity", INT, {}),
            ("unit_price", CUR, {"formatString": EUR2}),
            ("discount_rate", DEC, {"formatString": PCT, "summarizeBy": "none"}),
            ("gross_amount", CUR, {"formatString": EUR2}),
            ("discount_amount", CUR, {"formatString": EUR2}),
            ("net_amount", CUR, {"formatString": EUR2}),
            ("cost_amount", CUR, {"formatString": EUR2}),
            ("margin_amount", CUR, {"formatString": EUR2}),
        ],
    ),
    "Calendar": (
        "dim_date.csv",
        [
            ("date_key", INT, {"summarizeBy": "none", "isHidden": True}),
            ("full_date", DATE, {"formatString": "yyyy-mm-dd", "isKey": True}),
            ("year", INT, {"summarizeBy": "none"}),
            ("quarter", INT, {"summarizeBy": "none"}),
            ("month", INT, {"summarizeBy": "none", "isHidden": True}),
            ("month_key", INT, {"summarizeBy": "none", "isHidden": True}),
            ("month_start", DATE, {"formatString": "yyyy-mm-dd"}),
            ("month_name", TEXT, {"sortByColumn": "month"}),
            ("year_month", TEXT, {"sortByColumn": "month_key"}),
            ("day_of_month", INT, {"summarizeBy": "none"}),
            ("iso_weekday", INT, {"summarizeBy": "none"}),
            ("is_weekend", INT, {"summarizeBy": "none"}),
        ],
    ),
    "Product": (
        "dim_product.csv",
        [
            ("product_id", TEXT, {}),
            ("product_name", TEXT, {}),
            ("category", TEXT, {}),
            ("unit_cost", CUR, {"formatString": EUR2, "summarizeBy": "none"}),
            ("list_price", CUR, {"formatString": EUR2, "summarizeBy": "none"}),
            ("launch_date", DATE, {"formatString": "yyyy-mm-dd"}),
        ],
    ),
    "Quality Checks": (
        "quality_checks.csv",
        [
            ("name", TEXT, {}),
            ("category", TEXT, {}),
            ("passed", TEXT, {}),
            ("expected", TEXT, {}),
            ("actual", TEXT, {}),
            ("detail", TEXT, {}),
        ],
    ),
    "Rejected Rows": (
        "rejected_rows.csv",
        [
            ("source_table", TEXT, {}),
            ("source_line", INT, {"summarizeBy": "none"}),
            ("reason", TEXT, {}),
            ("column_name", TEXT, {}),
            ("message", TEXT, {}),
        ],
    ),
}

RELATIONSHIPS = [("Sales", "date_key", "Calendar", "date_key"), ("Sales", "product_id", "Product", "product_id")]

COMPLETED = "Sales[is_completed] = 1"
#: (name, DAX, format, display folder, description)
MEASURES: list[tuple[str, str, str, str, str]] = [
    (
        "Net Sales",
        f"CALCULATE ( SUM ( Sales[net_amount] ), {COMPLETED} )",
        EUR,
        "Sales",
        "Net line amounts (after discount) of completed orders",
    ),
    (
        "Gross Sales",
        f"CALCULATE ( SUM ( Sales[gross_amount] ), {COMPLETED} )",
        EUR,
        "Sales",
        "Quantity x unit price of completed orders, before discount",
    ),
    (
        "Discount Amount",
        f"CALCULATE ( SUM ( Sales[discount_amount] ), {COMPLETED} )",
        EUR,
        "Sales",
        "Discounts granted on completed orders",
    ),
    ("Orders", f"CALCULATE ( DISTINCTCOUNT ( Sales[order_id] ), {COMPLETED} )", COUNT, "Volume", "Completed orders"),
    (
        "Units Sold",
        f"CALCULATE ( SUM ( Sales[quantity] ), {COMPLETED} )",
        COUNT,
        "Volume",
        "Quantity of completed order lines",
    ),
    (
        "Active Customers",
        f"CALCULATE ( DISTINCTCOUNT ( Sales[customer_id] ), {COMPLETED} )",
        COUNT,
        "Volume",
        "Distinct customers with a completed order",
    ),
    (
        "Cancelled Orders",
        "CALCULATE ( DISTINCTCOUNT ( Sales[order_id] ), Sales[is_completed] = 0 )",
        COUNT,
        "Volume",
        "Cancelled orders",
    ),
    (
        "Cancellation Rate",
        "DIVIDE ( [Cancelled Orders], [Orders] + [Cancelled Orders] )",
        PCT,
        "Volume",
        "Cancelled orders / all orders",
    ),
    ("Average Order Value", "DIVIDE ( [Net Sales], [Orders] )", EUR2, "Sales", "Net sales / orders"),
    (
        "Gross Margin",
        f"CALCULATE ( SUM ( Sales[margin_amount] ), {COMPLETED} )",
        EUR,
        "Profitability",
        "Net sales - quantity x catalog unit cost",
    ),
    ("Gross Margin %", "DIVIDE ( [Gross Margin], [Net Sales] )", PCT, "Profitability", "Gross margin / net sales"),
    ("Discount Rate", "DIVIDE ( [Discount Amount], [Gross Sales] )", PCT, "Sales", "Discount amount / gross sales"),
    (
        "Net Sales PM",
        "CALCULATE ( [Net Sales], DATEADD ( Calendar[full_date], -1, MONTH ) )",
        EUR,
        "Comparisons",
        "Net sales of the previous month (same filter context)",
    ),
    (
        "MoM Growth %",
        "DIVIDE ( [Net Sales] - [Net Sales PM], [Net Sales PM] )",
        PCT,
        "Comparisons",
        "Change versus the previous month",
    ),
    (
        "Net Sales PY",
        "CALCULATE ( [Net Sales], SAMEPERIODLASTYEAR ( Calendar[full_date] ) )",
        EUR,
        "Comparisons",
        "Net sales of the same period one year earlier",
    ),
    (
        "YoY Growth %",
        "DIVIDE ( [Net Sales] - [Net Sales PY], [Net Sales PY] )",
        PCT,
        "Comparisons",
        "Change versus the same period one year earlier",
    ),
    ("Net Sales YTD", "TOTALYTD ( [Net Sales], Calendar[full_date] )", EUR, "Comparisons", "Year-to-date net sales"),
    (
        "Latest Month MoM %",
        "VAR LastMonth = CALCULATE ( MAX ( Calendar[month_key] ), ALLSELECTED ( Calendar ) ) "
        "RETURN CALCULATE ( [MoM Growth %], Calendar[month_key] = LastMonth )",
        PCT,
        "Comparisons",
        "Growth of the last selected month versus the month before",
    ),
    (
        "Latest Month YoY %",
        "VAR LastMonth = CALCULATE ( MAX ( Calendar[month_key] ), ALLSELECTED ( Calendar ) ) "
        "RETURN CALCULATE ( [YoY Growth %], Calendar[month_key] = LastMonth )",
        PCT,
        "Comparisons",
        "Growth of the last selected month versus the same month one year earlier",
    ),
    (
        "Category Share %",
        "DIVIDE ( [Net Sales], CALCULATE ( [Net Sales], ALL ( Product[category] ) ) )",
        PCT,
        "Sales",
        "Share of the net sales of all categories (other filters kept)",
    ),
    (
        "Product Rank",
        "IF ( NOT ISBLANK ( [Net Sales] ), RANKX ( ALLSELECTED ( Product[product_name] ), [Net Sales] ) )",
        "0",
        "Sales",
        "Rank of the product by net sales within the current selection",
    ),
    (
        "Rejected Rows",
        "COUNTROWS ( 'Rejected Rows' ) + 0",
        COUNT,
        "Data quality",
        "Source rows excluded by the pipeline (each with its reason)",
    ),
    (
        "Checks Passed",
        "COUNTROWS ( FILTER ( 'Quality Checks', 'Quality Checks'[passed] = \"true\" ) ) + 0",
        COUNT,
        "Data quality",
        "Quality and reconciliation checks that passed in the last run",
    ),
    (
        "Checks Failed",
        "COUNTROWS ( FILTER ( 'Quality Checks', 'Quality Checks'[passed] = \"false\" ) ) + 0",
        COUNT,
        "Data quality",
        "Checks that failed (a published run always has 0)",
    ),
]


def guid(*parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, "/".join(parts)))


def tmdl_name(name: str) -> str:
    return f"'{name}'" if not name.replace("_", "").isalnum() else name


def m_source(table: str) -> str:
    csv_file, columns = TABLES[table]
    types = ", ".join(f'{{"{c}", {M_TYPE[t]}}}' for c, t, _ in columns)
    return "\n".join(
        [
            "let",
            f'    Source = Csv.Document(File.Contents(DataFolder & "\\{csv_file}"), '
            '[Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),',
            "    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
            f'    Typed = Table.TransformColumnTypes(Headers, {{{types}}}, "en-US")',
            "in",
            "    Typed",
        ]
    )


def table_tmdl(table: str) -> str:
    _, columns = TABLES[table]
    lines = [f"table {tmdl_name(table)}", f"\tlineageTag: {guid('table', table)}"]
    if table == "Calendar":
        lines.append("\tdataCategory: Time")
    lines.append("")
    if table == "Sales":
        for name, dax, fmt, folder, description in MEASURES:
            lines += [
                f"\t/// {description}",
                f"\tmeasure '{name}' = {dax}",
                f"\t\tformatString: {fmt}",
                f"\t\tdisplayFolder: {folder}",
                f"\t\tlineageTag: {guid('measure', name)}",
                "",
            ]
    for name, dtype, extra in columns:
        lines += [f"\tcolumn {name}", f"\t\tdataType: {dtype}"]
        if extra.get("isKey"):
            lines.append("\t\tisKey")
        if extra.get("isHidden"):
            lines.append("\t\tisHidden")
        if "formatString" in extra:
            lines.append(f"\t\tformatString: {extra['formatString']}")
        lines.append(f"\t\tlineageTag: {guid('column', table, name)}")
        summarize = extra.get("summarizeBy", "sum" if dtype in (INT, DEC, CUR) else "none")
        lines.append(f"\t\tsummarizeBy: {summarize}")
        lines.append(f"\t\tsourceColumn: {name}")
        if "sortByColumn" in extra:
            lines.append(f"\t\tsortByColumn: {extra['sortByColumn']}")
        lines += ["", "\t\tannotation SummarizationSetBy = Automatic", ""]
    lines += [f"\tpartition {tmdl_name(table)} = m", "\t\tmode: import", "\t\tsource ="]
    lines += [f"\t\t\t\t{line}" for line in m_source(table).splitlines()]
    lines += ["", "\tannotation PBI_ResultType = Table", ""]
    return "\n".join(lines)


def model_files(data_folder: str) -> dict[str, str]:
    files = {
        "definition/database.tmdl": "database\n\tcompatibilityLevel: 1567\n",
        "definition/model.tmdl": "\n".join(
            [
                "model Model",
                "\tculture: en-US",
                "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
                "\tsourceQueryCulture: en-US",
                "\tdataAccessOptions",
                "\t\tlegacyRedirects",
                "\t\treturnErrorValuesAsNull",
                "",
                "annotation __PBI_TimeIntelligenceEnabled = 0",
                "",
                "annotation PBI_QueryOrder = " + json.dumps(["DataFolder", *TABLES]),
                "",
                *[f"ref table {tmdl_name(t)}" for t in TABLES],
                "",
            ]
        ),
        "definition/expressions.tmdl": "\n".join(
            [
                f'expression DataFolder = "{data_folder}" meta [IsParameterQuery = true, Type = "Text", '
                "IsParameterQueryRequired = true]",
                f"\tlineageTag: {guid('expression', 'DataFolder')}",
                "",
                "\tannotation PBI_ResultType = Text",
                "",
            ]
        ),
        "definition/relationships.tmdl": "\n".join(
            line
            for from_t, from_c, to_t, to_c in RELATIONSHIPS
            for line in (
                f"relationship {guid('relationship', from_t, from_c)}",
                f"\tfromColumn: {tmdl_name(from_t)}.{from_c}",
                f"\ttoColumn: {tmdl_name(to_t)}.{to_c}",
                "",
            )
        ),
        "definition.pbism": json.dumps({"version": "4.2", "settings": {}}, indent=2) + "\n",
    }
    for table in TABLES:
        files[f"definition/tables/{table}.tmdl"] = table_tmdl(table)
    return files


# --------------------------------------------------------------------------- report


def lit(value: str | float | bool) -> dict:
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int | float):
        text = f"{value}D"
    else:
        text = "'" + value.replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": text}}}


def measure(name: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": "Sales"}}, "Property": name}}


def column(table: str, name: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}}


def field_ref(spec: str) -> tuple[dict, str]:
    """'[Net Sales]' -> measure; 'Product.category' -> column."""
    if spec.startswith("["):
        name = spec[1:-1]
        return measure(name), f"Sales.{name}"
    table, name = spec.split(".", 1)
    return column(table, name), f"{table}.{name}"


def visual(
    name: str,
    vtype: str,
    x: int,
    y: int,
    w: int,
    h: int,
    roles: dict[str, list[str]] | None = None,
    title: str | None = None,
    sort: tuple[str, str] | None = None,
    objects: dict | None = None,
) -> dict:
    body: dict = {"visualType": vtype, "drillFilterOtherVisuals": True}
    if roles:
        state = {}
        for role, specs in roles.items():
            projections = []
            for spec in specs:
                field, ref = field_ref(spec)
                projections.append({"field": field, "queryRef": ref, "nativeQueryRef": ref.split(".", 1)[1]})
            state[role] = {"projections": projections}
        body["query"] = {"queryState": state}
        if sort:
            body["query"]["sortDefinition"] = {
                "sort": [{"field": field_ref(sort[0])[0], "direction": sort[1]}],
                "isDefaultSort": False,
            }
    if objects:
        body["objects"] = objects
    container = {"title": [{"properties": {"show": lit(bool(title)), **({"text": lit(title)} if title else {})}}]}
    if vtype != "textbox":
        container["border"] = [{"properties": {"show": lit(True), "radius": lit(6)}}]
    body["visualContainerObjects"] = container
    return {
        "$schema": f"{SCHEMA}/visualContainer/{VERSIONS['visual']}/schema.json",
        "name": name,
        "position": {"x": x, "y": y, "z": 0, "width": w, "height": h, "tabOrder": 0},
        "visual": body,
    }


def textbox(
    name: str, x: int, y: int, w: int, h: int, text: str, size: str, bold: bool = False, color: str = "#1F2937"
) -> dict:
    style = {"fontSize": size, "color": color, **({"fontWeight": "bold"} if bold else {})}
    objects = {"general": [{"properties": {"paragraphs": [{"textRuns": [{"value": text, "textStyle": style}]}]}}]}
    return visual(name, "textbox", x, y, w, h, objects=objects)


def slicer(name: str, x: int, field: str, title: str) -> dict:
    return visual(
        name,
        "slicer",
        x,
        16,
        170,
        56,
        {"Values": [field]},
        title,
        objects={"data": [{"properties": {"mode": lit("Dropdown")}}], "header": [{"properties": {"show": lit(False)}}]},
    )


def card(name: str, x: int, y: int, measure_name: str, title: str, units: int = 1, precision: int = 0) -> dict:
    """KPI card; ``units`` 1 = exact value, 1000000 = millions (with ``precision`` decimals)."""
    labels = {"fontSize": lit(24), "labelDisplayUnits": lit(units), "labelPrecision": lit(precision)}
    return visual(
        name,
        "card",
        x,
        y,
        238,
        104,
        {"Values": [f"[{measure_name}]"]},
        title,
        objects={"labels": [{"properties": labels}], "categoryLabels": [{"properties": {"show": lit(False)}}]},
    )


def header(prefix: str, title: str, subtitle: str) -> list[dict]:
    return [
        textbox(f"{prefix}_title", 24, 12, 620, 40, title, "20pt", bold=True),
        textbox(f"{prefix}_subtitle", 24, 50, 620, 28, subtitle, "10pt", color="#6B7280"),
        slicer(f"{prefix}_year", 700, "Calendar.year", "Year"),
        slicer(f"{prefix}_category", 880, "Product.category", "Category"),
        slicer(f"{prefix}_segment", 1060, "Sales.customer_segment", "Segment"),
    ]


PAGES: list[tuple[str, str, list[dict]]] = [
    (
        "overview",
        "Overview",
        [
            *header("ov", "Monthly sales performance", "Synthetic demo data - EUR - completed orders only"),
            card("ov_net_sales", 24, 92, "Net Sales", "Net sales", units=1000000, precision=2),
            card("ov_orders", 272, 92, "Orders", "Orders"),
            card("ov_units", 520, 92, "Units Sold", "Units sold"),
            card("ov_aov", 768, 92, "Average Order Value", "Average order value", precision=2),
            card("ov_margin", 1016, 92, "Gross Margin %", "Gross margin %", precision=1),
            visual(
                "ov_trend",
                "lineChart",
                24,
                212,
                760,
                250,
                {"Category": ["Calendar.year_month"], "Y": ["[Net Sales]", "[Net Sales PY]"]},
                "Net sales by month vs same month last year",
            ),
            visual(
                "ov_category",
                "clusteredBarChart",
                800,
                212,
                456,
                250,
                {"Category": ["Product.category"], "Y": ["[Net Sales]"]},
                "Net sales by category",
                sort=("[Net Sales]", "Descending"),
            ),
            visual(
                "ov_products",
                "tableEx",
                24,
                476,
                760,
                232,
                {
                    "Values": [
                        "Product.product_name",
                        "Product.category",
                        "[Net Sales]",
                        "[Units Sold]",
                        "[Gross Margin %]",
                        "[Product Rank]",
                    ]
                },
                "Product ranking",
                sort=("[Net Sales]", "Descending"),
            ),
            visual(
                "ov_segment",
                "clusteredColumnChart",
                800,
                476,
                456,
                232,
                {"Category": ["Sales.customer_segment"], "Y": ["[Net Sales]"]},
                "Net sales by customer segment",
                sort=("[Net Sales]", "Descending"),
            ),
        ],
    ),
    (
        "trends",
        "Trends and comparisons",
        [
            *header("tr", "Trends and period comparisons", "Month over month, year over year and year to date"),
            card("tr_mom", 24, 92, "Latest Month MoM %", "Latest month MoM", precision=1),
            card("tr_yoy", 272, 92, "Latest Month YoY %", "Latest month YoY", precision=1),
            card("tr_ytd", 520, 92, "Net Sales YTD", "Net sales YTD", units=1000000, precision=2),
            card("tr_customers", 768, 92, "Active Customers", "Active customers"),
            card("tr_cancel", 1016, 92, "Cancellation Rate", "Cancellation rate", precision=1),
            visual(
                "tr_monthly",
                "tableEx",
                24,
                212,
                700,
                496,
                {
                    "Values": [
                        "Calendar.year_month",
                        "[Net Sales]",
                        "[Net Sales PM]",
                        "[MoM Growth %]",
                        "[Net Sales PY]",
                        "[YoY Growth %]",
                        "[Orders]",
                        "[Average Order Value]",
                    ]
                },
                "Monthly figures",
                sort=("Calendar.year_month", "Ascending"),
            ),
            visual(
                "tr_share",
                "lineChart",
                740,
                212,
                516,
                240,
                {"Category": ["Calendar.year_month"], "Y": ["[Category Share %]"], "Series": ["Product.category"]},
                "Category share of monthly net sales",
            ),
            visual(
                "tr_margin",
                "lineChart",
                740,
                468,
                516,
                240,
                {"Category": ["Calendar.year_month"], "Y": ["[Gross Margin %]", "[Discount Rate]"]},
                "Gross margin % and discount rate by month",
            ),
        ],
    ),
    (
        "quality",
        "Data quality",
        [
            textbox("dq_title", 24, 12, 900, 40, "Data quality of the last pipeline run", "20pt", bold=True),
            textbox(
                "dq_subtitle",
                24,
                50,
                900,
                28,
                "Rows excluded from the model and the checks run before publication",
                "10pt",
                color="#6B7280",
            ),
            card("dq_rejected", 24, 92, "Rejected Rows", "Rejected source rows"),
            card("dq_passed", 272, 92, "Checks Passed", "Checks passed"),
            card("dq_failed", 520, 92, "Checks Failed", "Checks failed"),
            visual(
                "dq_reasons",
                "clusteredBarChart",
                24,
                212,
                500,
                496,
                {
                    "Category": ["Rejected Rows.reason"],
                    "Y": ["[Rejected Rows]"],
                    "Series": ["Rejected Rows.source_table"],
                },
                "Rejected rows by reason",
                sort=("[Rejected Rows]", "Descending"),
            ),
            visual(
                "dq_checks",
                "tableEx",
                540,
                212,
                716,
                496,
                {
                    "Values": [
                        "Quality Checks.name",
                        "Quality Checks.category",
                        "Quality Checks.passed",
                        "Quality Checks.expected",
                        "Quality Checks.actual",
                    ]
                },
                "Checks run before publication",
            ),
        ],
    ),
]


def theme() -> dict:
    return {
        "name": "Sales Reporting",
        "dataColors": ["#1F4E79", "#2E86AB", "#F18F01", "#C73E1D", "#3B8B5A", "#6C5B7B", "#8D99AE", "#E9C46A"],
        "background": "#FFFFFF",
        "foreground": "#1F2937",
        "tableAccent": "#1F4E79",
        "good": "#3B8B5A",
        "bad": "#C73E1D",
        "neutral": "#F18F01",
        "textClasses": {
            "title": {"fontFace": "Segoe UI Semibold", "fontSize": 12, "color": "#1F2937"},
            "label": {"fontFace": "Segoe UI", "fontSize": 10, "color": "#374151"},
            "callout": {"fontFace": "Segoe UI Semibold", "fontSize": 24, "color": "#1F4E79"},
        },
    }


def report_files(active_page: str = PAGES[0][0]) -> dict[str, str]:
    def dump(data: dict) -> str:
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    theme_file = "SalesReportingTheme.json"
    files = {
        "definition.pbir": dump(
            {"version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}}
        ),
        "definition/version.json": dump({"$schema": f"{SCHEMA}/versionMetadata/1.0.0/schema.json", "version": "2.0.0"}),
        "definition/report.json": dump(
            {
                "$schema": f"{SCHEMA}/report/{VERSIONS['report']}/schema.json",
                "themeCollection": {
                    "baseTheme": {"name": BASE_THEME, "reportVersionAtImport": VERSIONS, "type": "SharedResources"},
                    "customTheme": {
                        "name": theme_file,
                        "reportVersionAtImport": VERSIONS,
                        "type": "RegisteredResources",
                    },
                },
                "resourcePackages": [
                    {
                        "name": "RegisteredResources",
                        "type": "RegisteredResources",
                        "items": [{"name": theme_file, "path": theme_file, "type": "CustomTheme"}],
                    }
                ],
                "objects": {"outspacePane": [{"properties": {"expanded": lit(False)}}]},
                "settings": {"useStylableVisualContainerHeader": True, "defaultDrillFilterOtherVisuals": True},
            }
        ),
        f"StaticResources/RegisteredResources/{theme_file}": dump(theme()),
        "definition/pages/pages.json": dump(
            {
                "$schema": f"{SCHEMA}/pagesMetadata/1.1.0/schema.json",
                "pageOrder": [p[0] for p in PAGES],
                "activePageName": active_page,
            }
        ),
    }
    for page_name, display, visuals in PAGES:
        files[f"definition/pages/{page_name}/page.json"] = dump(
            {
                "$schema": f"{SCHEMA}/page/{VERSIONS['page']}/schema.json",
                "name": page_name,
                "displayName": display,
                "displayOption": "FitToPage",
                "height": 720,
                "width": 1280,
                "objects": {
                    "background": [
                        {"properties": {"color": {"solid": {"color": lit("#F5F7FA")}}, "transparency": lit(0)}}
                    ]
                },
            }
        )
        for order, item in enumerate(visuals):
            item = json.loads(json.dumps(item))
            item["position"]["tabOrder"] = order * 1000
            item["position"]["z"] = order * 1000
            files[f"definition/pages/{page_name}/visuals/{item['name']}/visual.json"] = dump(item)
    return files


def measures_dax() -> str:
    lines = [
        "// DAX measures of the Power BI model (generated by scripts/build_powerbi_project.py).",
        "// All revenue measures count completed orders only (Sales[is_completed] = 1).",
        "",
    ]
    for name, dax, fmt, folder, description in MEASURES:
        lines += [f"// {description}  [folder: {folder}; format: {fmt}]", f"[{name}] :=", f"    {dax}", ""]
    return "\n".join(lines)


def write(output: Path, data_folder: str, active_page: str = PAGES[0][0]) -> list[Path]:
    if output.exists():
        shutil.rmtree(output)
    written = []
    groups = {f"{NAME}.SemanticModel": model_files(data_folder), f"{NAME}.Report": report_files(active_page)}
    for folder, files in groups.items():
        for relative, text in files.items():
            path = output / folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
            written.append(path)
    pbip = {
        "version": "1.0",
        "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
        "settings": {"enableAutoRecovery": True},
    }
    (output / f"{NAME}.pbip").write_bytes((json.dumps(pbip, indent=2) + "\n").encode("utf-8"))
    (output / ".gitignore").write_bytes(b"**/.pbi/localSettings.json\n**/.pbi/cache.abf\n")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=ROOT / "powerbi" / "project")
    parser.add_argument("--data-folder", default=PLACEHOLDER, help="absolute folder of the exported CSV files")
    parser.add_argument(
        "--active-page", default=PAGES[0][0], choices=[p[0] for p in PAGES], help="page shown when the report opens"
    )
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else (Path.cwd() / args.output)
    write(output, args.data_folder.replace("/", "\\"), args.active_page)
    (ROOT / "powerbi" / "measures.dax").write_bytes(measures_dax().encode("utf-8"))
    (ROOT / "powerbi" / "theme.json").write_bytes((json.dumps(theme(), indent=2) + "\n").encode("utf-8"))
    print(f"Power BI project written to {output} (data folder: {args.data_folder})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
