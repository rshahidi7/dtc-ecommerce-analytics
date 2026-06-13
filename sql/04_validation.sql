-- ============================================================================
-- 04_validation.sql
-- Data-quality and integrity checks for the star schema. These are read-only
-- SELECTs meant to be run interactively after building the schema (steps 01-02)
-- to confirm grain, null-safety, uniqueness, and referential integrity.
-- ============================================================================


-- One row per order (total_rows should equal distinct_orders).
SELECT COUNT(*) AS total_rows,
       COUNT(DISTINCT order_id) AS distinct_orders
FROM fact_orders;

-- Orders with no customer key (investigate if non-zero).
SELECT COUNT(*) AS orders_missing_customer
FROM fact_orders
WHERE customer_id IS NULL;

-- Order dates that do not resolve to a dim_date row (should be 0).
SELECT COUNT(*) AS orders_missing_date
FROM fact_orders f
LEFT JOIN dim_date d
    ON f.order_date_key = d.date_key
WHERE d.date_key IS NULL;

-- Dimension uniqueness: customer_id must be unique in dim_customers (0 rows).
SELECT customer_id, COUNT(*)
FROM dim_customers
GROUP BY customer_id
HAVING COUNT(*) > 1;

-- Dimension uniqueness: date_key must be unique in dim_date (0 rows).
SELECT date_key, COUNT(*)
FROM dim_date
GROUP BY date_key
HAVING COUNT(*) > 1;

-- Referential integrity: every fact_orders.customer_id exists in dim_customers.
SELECT COUNT(*) AS orphan_customer_keys
FROM fact_orders f
LEFT JOIN dim_customers c
    ON f.customer_id = c.customer_id
WHERE c.customer_id IS NULL;

-- Revenue verification: fact total should reconcile with the raw orders total.
SELECT SUM(order_revenue) AS fact_orders_revenue FROM fact_orders;
SELECT SUM("Total") AS raw_orders_revenue FROM orders;

-- Duplicate orders in the fact table (should return 0 rows).
SELECT order_id, COUNT(*)
FROM fact_orders
GROUP BY order_id
HAVING COUNT(*) > 1;
