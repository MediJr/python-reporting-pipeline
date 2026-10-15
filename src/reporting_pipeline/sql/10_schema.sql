-- Staging area and star schema. Rebuilt by every run inside one transaction (see docs/architecture.md).
-- Portable SQL: runs unchanged on SQLite and PostgreSQL. {{DATE}} is DATE on PostgreSQL, TEXT (ISO 8601) on SQLite.
-- Amounts are integer cents, rates are integer basis points (1 bp = 0.01 %): exact on both engines.

DROP TABLE IF EXISTS kpi_summary;
DROP TABLE IF EXISTS mart_segment_region;
DROP TABLE IF EXISTS mart_product_performance;
DROP TABLE IF EXISTS mart_category_monthly;
DROP TABLE IF EXISTS mart_monthly_sales;
DROP TABLE IF EXISTS fact_sales;
DROP TABLE IF EXISTS dim_product;
DROP TABLE IF EXISTS dim_date;
DROP TABLE IF EXISTS rejected_rows;
DROP TABLE IF EXISTS stg_order_lines;
DROP TABLE IF EXISTS stg_orders;
DROP TABLE IF EXISTS stg_products;

-- ---------------------------------------------------------------- staging (typed rows accepted by the CSV checks)

CREATE TABLE stg_products (
    product_id       TEXT    NOT NULL PRIMARY KEY,
    product_name     TEXT    NOT NULL,
    category         TEXT    NOT NULL,
    unit_cost_cents  BIGINT  NOT NULL CHECK (unit_cost_cents >= 0),
    list_price_cents BIGINT  NOT NULL CHECK (list_price_cents >= 0),
    launch_date      {{DATE}} NOT NULL,
    source_line      INTEGER NOT NULL
);

CREATE TABLE stg_orders (
    order_id         TEXT    NOT NULL PRIMARY KEY,
    order_date       {{DATE}} NOT NULL,
    customer_id      TEXT    NOT NULL,
    customer_segment TEXT    NOT NULL,
    region           TEXT    NOT NULL,
    sales_channel    TEXT    NOT NULL,
    order_status     TEXT    NOT NULL CHECK (order_status IN ('completed', 'cancelled')),
    source_line      INTEGER NOT NULL
);

CREATE TABLE stg_order_lines (
    order_id         TEXT    NOT NULL,
    line_number      INTEGER NOT NULL CHECK (line_number >= 1),
    product_id       TEXT    NOT NULL,
    quantity         INTEGER NOT NULL CHECK (quantity >= 1),
    unit_price_cents BIGINT  NOT NULL CHECK (unit_price_cents >= 0),
    discount_bp      INTEGER NOT NULL CHECK (discount_bp BETWEEN 0 AND 10000),
    source_line      INTEGER NOT NULL,
    PRIMARY KEY (order_id, line_number)
);

-- every excluded source row, with its reason (CSV checks and referential integrity)
CREATE TABLE rejected_rows (
    source_table TEXT    NOT NULL,
    source_line  INTEGER NOT NULL,
    reason       TEXT    NOT NULL,
    column_name  TEXT,
    message      TEXT    NOT NULL
);

-- ---------------------------------------------------------------- star schema

CREATE TABLE dim_date (
    date_key        INTEGER  NOT NULL PRIMARY KEY,  -- YYYYMMDD
    full_date       {{DATE}} NOT NULL UNIQUE,
    year            INTEGER  NOT NULL,
    quarter         INTEGER  NOT NULL,
    month           INTEGER  NOT NULL,
    month_key       INTEGER  NOT NULL,              -- YYYYMM
    month_start     {{DATE}} NOT NULL,
    month_name      TEXT     NOT NULL,
    year_month      TEXT     NOT NULL,              -- e.g. 2025-03
    day_of_month    INTEGER  NOT NULL,
    iso_weekday     INTEGER  NOT NULL,              -- 1 = Monday
    is_weekend      INTEGER  NOT NULL CHECK (is_weekend IN (0, 1))
);

CREATE TABLE dim_product (
    product_id       TEXT     NOT NULL PRIMARY KEY,
    product_name     TEXT     NOT NULL,
    category         TEXT     NOT NULL,
    unit_cost_cents  BIGINT   NOT NULL,
    list_price_cents BIGINT   NOT NULL,
    launch_date      {{DATE}} NOT NULL
);

-- grain: one row per order line (order_id, line_number)
CREATE TABLE fact_sales (
    order_id              TEXT     NOT NULL,
    line_number           INTEGER  NOT NULL,
    date_key              INTEGER  NOT NULL REFERENCES dim_date (date_key),
    order_date            {{DATE}} NOT NULL,
    product_id            TEXT     NOT NULL REFERENCES dim_product (product_id),
    customer_id           TEXT     NOT NULL,
    customer_segment      TEXT     NOT NULL,
    region                TEXT     NOT NULL,
    sales_channel         TEXT     NOT NULL,
    order_status          TEXT     NOT NULL,
    is_completed          INTEGER  NOT NULL CHECK (is_completed IN (0, 1)),
    quantity              INTEGER  NOT NULL,
    unit_price_cents      BIGINT   NOT NULL,
    discount_rate_bp      INTEGER  NOT NULL,
    gross_amount_cents    BIGINT   NOT NULL,
    discount_amount_cents BIGINT   NOT NULL,
    net_amount_cents      BIGINT   NOT NULL,
    cost_amount_cents     BIGINT   NOT NULL,
    margin_amount_cents   BIGINT   NOT NULL,
    PRIMARY KEY (order_id, line_number)
);
