-- ============================================================================
-- 02_derived_tables.sql
-- Derived / aggregate tables built on top of the star schema:
--   * cohort augmentation of fact_orders (cohort_month, cohort_index)
--   * orders_cohort (standalone order-level cohort table)
--   * inventory_health (sell-through by product category)
--
-- Run after 01_star_schema.sql. The cohort columns added here are required by
-- the cohort_retention view in 03_views.sql.
--
-- Note: the original script's incomplete product_repeat_purchase block (a CTE
-- that was never finished) has been removed.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- orders_cohort: order-level table tagging each order with its customer's
-- acquisition cohort month and the month offset from that cohort.
-- ----------------------------------------------------------------------------
CREATE TABLE orders_cohort AS
SELECT
    o.customer_id,
    o.order_date_key,
    DATE_TRUNC('month', o.order_date_key) AS order_month,
    DATE_TRUNC('month', MIN(o.order_date_key) OVER (PARTITION BY o.customer_id)) AS cohort_month,
    DATE_PART('month', AGE(o.order_date_key, MIN(o.order_date_key) OVER (PARTITION BY o.customer_id))) AS cohort_index
FROM fact_orders o;


-- ----------------------------------------------------------------------------
-- Augment fact_orders with cohort columns so cohort analysis can run directly
-- off the fact table.
-- ----------------------------------------------------------------------------
ALTER TABLE fact_orders ADD COLUMN cohort_month DATE;

ALTER TABLE fact_orders ADD COLUMN cohort_index INT;

UPDATE fact_orders o
SET
    cohort_month = sub.cohort_month,
    cohort_index = sub.cohort_index
FROM (
    SELECT
        customer_id,
        order_date_key,
        DATE_TRUNC('month', order_date_key) AS order_month,
        DATE_TRUNC('month', MIN(order_date_key) OVER (PARTITION BY customer_id)) AS cohort_month,
        DATE_PART(
            'month',
            AGE(
                DATE_TRUNC('month', order_date_key),
                DATE_TRUNC('month', MIN(order_date_key) OVER (PARTITION BY customer_id))
            )
        ) AS cohort_index
    FROM fact_orders
) sub
WHERE o.customer_id = sub.customer_id
  AND o.order_date_key = sub.order_date_key;


-- ----------------------------------------------------------------------------
-- inventory_health: sell-through rate by product category.
-- Source table expected to exist: products_by_percentage_sold.
-- ----------------------------------------------------------------------------
CREATE TABLE inventory_health AS
SELECT
    p.product_category,
    SUM(i."Inventory units sold"::int) AS total_units_sold,
    SUM(i."Starting inventory units"::int) AS total_starting_inventory,
    ROUND(
        100.0 * SUM(i."Inventory units sold"::int) / NULLIF(SUM(i."Starting inventory units"::int), 0),
        1
    ) AS sell_through_pct
FROM products_by_percentage_sold i
LEFT JOIN dim_products p ON p.raw_product_name = i."Product title"
WHERE i."Starting inventory units"::int > 0
  AND p.product_category NOT IN ('Other', 'Sample', 'Gift Card', 'Package Protection')
GROUP BY p.product_category
ORDER BY sell_through_pct DESC;
