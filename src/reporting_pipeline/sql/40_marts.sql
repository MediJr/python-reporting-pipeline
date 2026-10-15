-- Reporting tables (marts) built from the star schema. Revenue figures count completed orders only;
-- cancelled orders are counted separately. All sums are cast to BIGINT so that both engines return integers.

-- one row per calendar month of dim_date (months without sales show zeros, never a gap)
CREATE TABLE mart_monthly_sales AS
WITH months AS (
    SELECT DISTINCT month_key, month_start, year_month FROM dim_date
),
monthly AS (
    SELECT
        m.month_key,
        m.month_start,
        m.year_month,
        COUNT(DISTINCT CASE WHEN f.is_completed = 1 THEN f.order_id END) AS orders,
        COUNT(DISTINCT CASE WHEN f.is_completed = 0 THEN f.order_id END) AS cancelled_orders,
        COUNT(DISTINCT CASE WHEN f.is_completed = 1 THEN f.customer_id END) AS active_customers,
        CAST(COALESCE(SUM(CASE WHEN f.is_completed = 1 THEN f.quantity END), 0) AS BIGINT) AS units,
        CAST(COALESCE(SUM(CASE WHEN f.is_completed = 1 THEN f.gross_amount_cents END), 0) AS BIGINT) AS gross_sales_cents,
        CAST(COALESCE(SUM(CASE WHEN f.is_completed = 1 THEN f.discount_amount_cents END), 0) AS BIGINT) AS discount_amount_cents,
        CAST(COALESCE(SUM(CASE WHEN f.is_completed = 1 THEN f.net_amount_cents END), 0) AS BIGINT) AS net_sales_cents,
        CAST(COALESCE(SUM(CASE WHEN f.is_completed = 1 THEN f.cost_amount_cents END), 0) AS BIGINT) AS cost_amount_cents,
        CAST(COALESCE(SUM(CASE WHEN f.is_completed = 1 THEN f.margin_amount_cents END), 0) AS BIGINT) AS gross_margin_cents
    FROM months m
    LEFT JOIN dim_date d ON d.month_key = m.month_key
    LEFT JOIN fact_sales f ON f.date_key = d.date_key
    GROUP BY m.month_key, m.month_start, m.year_month
)
SELECT
    cur.*,
    prev.net_sales_cents AS prev_month_net_sales_cents,
    py.net_sales_cents AS prev_year_net_sales_cents
FROM monthly cur
LEFT JOIN monthly prev
    ON prev.month_key = CASE WHEN cur.month_key % 100 = 1 THEN cur.month_key - 89 ELSE cur.month_key - 1 END
LEFT JOIN monthly py ON py.month_key = cur.month_key - 100;

-- one row per month and category; share of the month's net sales in basis points (rounded half-up)
CREATE TABLE mart_category_monthly AS
SELECT
    c.month_key,
    c.year_month,
    c.category,
    c.orders,
    c.units,
    c.net_sales_cents,
    c.gross_margin_cents,
    CASE WHEN m.net_sales_cents > 0
         THEN (2 * c.net_sales_cents * 10000 + m.net_sales_cents) / (2 * m.net_sales_cents)
    END AS share_of_month_bp
FROM (
    SELECT
        d.month_key,
        d.year_month,
        p.category,
        COUNT(DISTINCT f.order_id) AS orders,
        CAST(SUM(f.quantity) AS BIGINT) AS units,
        CAST(SUM(f.net_amount_cents) AS BIGINT) AS net_sales_cents,
        CAST(SUM(f.margin_amount_cents) AS BIGINT) AS gross_margin_cents
    FROM fact_sales f
    JOIN dim_date d ON d.date_key = f.date_key
    JOIN dim_product p ON p.product_id = f.product_id
    WHERE f.is_completed = 1
    GROUP BY d.month_key, d.year_month, p.category
) c
JOIN mart_monthly_sales m ON m.month_key = c.month_key;

-- one row per product (products without sales included); rankings by net sales
CREATE TABLE mart_product_performance AS
SELECT
    s.*,
    CASE WHEN s.net_sales_cents > 0
         THEN (2 * s.gross_margin_cents * 10000 + s.net_sales_cents) / (2 * s.net_sales_cents)
    END AS margin_rate_bp,
    RANK() OVER (ORDER BY s.net_sales_cents DESC) AS sales_rank,
    RANK() OVER (PARTITION BY s.category ORDER BY s.net_sales_cents DESC) AS category_rank
FROM (
    SELECT
        p.product_id,
        p.product_name,
        p.category,
        COUNT(DISTINCT f.order_id) AS orders,
        CAST(COALESCE(SUM(f.quantity), 0) AS BIGINT) AS units,
        CAST(COALESCE(SUM(f.net_amount_cents), 0) AS BIGINT) AS net_sales_cents,
        CAST(COALESCE(SUM(f.margin_amount_cents), 0) AS BIGINT) AS gross_margin_cents
    FROM dim_product p
    LEFT JOIN fact_sales f ON f.product_id = p.product_id AND f.is_completed = 1
    GROUP BY p.product_id, p.product_name, p.category
) s;

-- one row per customer segment and region, over the whole period
CREATE TABLE mart_segment_region AS
SELECT
    customer_segment,
    region,
    COUNT(DISTINCT order_id) AS orders,
    COUNT(DISTINCT customer_id) AS customers,
    CAST(SUM(quantity) AS BIGINT) AS units,
    CAST(SUM(net_amount_cents) AS BIGINT) AS net_sales_cents,
    CAST(SUM(margin_amount_cents) AS BIGINT) AS gross_margin_cents
FROM fact_sales
WHERE is_completed = 1
GROUP BY customer_segment, region;

-- KPI table: filled by the pipeline (Python computes period comparisons from mart_monthly_sales)
CREATE TABLE kpi_summary (
    position     INTEGER NOT NULL,
    kpi          TEXT   NOT NULL PRIMARY KEY,
    label        TEXT   NOT NULL,
    value        TEXT,
    unit         TEXT   NOT NULL,
    period       TEXT   NOT NULL,
    definition   TEXT   NOT NULL
);
