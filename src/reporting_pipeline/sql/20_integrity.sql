-- Referential integrity between the staged sources. Rows that fail are recorded in rejected_rows
-- (with their reason) and then left out of the model; nothing is removed silently.

-- order lines whose order is not in stg_orders (missing from the file, or rejected by the CSV checks)
INSERT INTO rejected_rows (source_table, source_line, reason, column_name, message)
SELECT 'order_lines', l.source_line, 'unknown_order', 'order_id',
       'order ' || l.order_id || ' is not an accepted order'
FROM stg_order_lines l
WHERE NOT EXISTS (SELECT 1 FROM stg_orders o WHERE o.order_id = l.order_id);

-- order lines whose product is not in the product catalog
INSERT INTO rejected_rows (source_table, source_line, reason, column_name, message)
SELECT 'order_lines', l.source_line, 'unknown_product', 'product_id',
       'product ' || l.product_id || ' is not in the product catalog'
FROM stg_order_lines l
WHERE EXISTS (SELECT 1 FROM stg_orders o WHERE o.order_id = l.order_id)
  AND NOT EXISTS (SELECT 1 FROM stg_products p WHERE p.product_id = l.product_id);
