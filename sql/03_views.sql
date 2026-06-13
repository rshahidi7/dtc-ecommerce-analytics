-- ============================================================================
-- 03_views.sql
-- Analytical views over the star schema: customer KPIs, monthly revenue,
-- order-behavior metrics, geographic performance, and cohort retention.
--
-- Run after 02_derived_tables.sql. The cohort_retention view depends on the
-- cohort_month / cohort_index columns added to fact_orders in step 02.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- customer_kpis: single-row summary of the customer base.
-- ----------------------------------------------------------------------------
CREATE VIEW customer_kpis AS
    SELECT
        COUNT(*) AS total_customers,

        SUM(CASE WHEN repeat_customer THEN 1 ELSE 0 END) AS repeat_customers,

        ROUND(AVG(net_lifetime_revenue), 2) AS avg_lifetime_value,

        ROUND(SUM(net_lifetime_revenue) / NULLIF(SUM(num_orders), 0), 2) AS avg_order_value,

        ROUND(CAST(SUM(CASE WHEN repeat_customer THEN 1 ELSE 0 END) AS NUMERIC) / COUNT(*), 3) AS repeat_rate,

        ROUND(AVG(num_orders), 2) AS avg_num_orders,

        SUM(net_lifetime_revenue) AS total_revenue,

        SUM(num_orders) AS total_orders,

        SUM(lifetime_returns) AS total_returns

    FROM dim_customers;


-- ----------------------------------------------------------------------------
-- monthly_revenue: revenue and order-behavior trends by month.
-- ----------------------------------------------------------------------------
CREATE VIEW monthly_revenue AS
    SELECT
        d."year" AS "year",

        d.month_name AS month_name,

        COUNT(o.order_id) AS total_orders,

        ROUND(CAST(SUM(o.order_revenue) / NULLIF(COUNT(o.order_id), 0) AS NUMERIC), 2) AS avg_order_value,

        ROUND(CAST(SUM(o.order_revenue + o.return_amount) AS NUMERIC), 2) AS net_revenue,

        ROUND(AVG(o.product_count), 2) AS avg_products_per_order,

        ROUND(AVG(o.sample_count), 2) AS avg_samples_per_order,

        ROUND(CAST(SUM(CASE WHEN o.discount_flag THEN 1 ELSE 0 END) AS NUMERIC) / COUNT(*), 3) AS discount_usage_rate,

        ROUND(CAST(SUM(CASE WHEN o.package_protection_flag THEN 1 ELSE 0 END) AS NUMERIC) / COUNT(*), 3) AS package_protection_rate

    FROM fact_orders o

    JOIN dim_date d ON o.order_date_key = d.date_key

    GROUP BY d."year", d."month", d.month_name

    ORDER BY d."year", d."month";


-- ----------------------------------------------------------------------------
-- order_behavior_metrics: single-row summary of order-level behavior.
-- ----------------------------------------------------------------------------
CREATE VIEW order_behavior_metrics AS
    SELECT
        ROUND(AVG(o.product_count), 2) AS avg_products_per_order,

        ROUND(AVG(o.sample_count), 2) AS avg_samples_per_order,

        ROUND(CAST(SUM(CASE WHEN o.discount_flag THEN 1 ELSE 0 END) AS NUMERIC) / COUNT(*), 3) AS discount_usage_rate,

        ROUND(CAST(SUM(CASE WHEN o.package_protection_flag THEN 1 ELSE 0 END) AS NUMERIC) / COUNT(*), 3) AS package_protection_rate,

        ROUND(CAST(SUM(CASE WHEN o.return_flag THEN 1 ELSE 0 END) AS NUMERIC) / COUNT(*), 3) AS returns_rate,

        ROUND(AVG(CAST(o.order_revenue AS NUMERIC)), 2) AS avg_order_revenue

    FROM fact_orders o;


-- ----------------------------------------------------------------------------
-- geographic_performance: order behavior by shipping state.
-- ----------------------------------------------------------------------------
CREATE VIEW geographic_performance AS
    SELECT
        o.state,

        COUNT(DISTINCT customer_id) AS total_customers,

        COUNT(o.order_id) AS num_orders,

        ROUND(AVG(o.product_count), 2) AS avg_products_per_order,

        ROUND(AVG(o.sample_count), 2) AS avg_samples_per_order,

        ROUND(AVG(CAST(o.order_revenue AS NUMERIC)), 2) AS avg_order_revenue,

        ROUND(CAST(SUM(CASE WHEN o.discount_flag THEN 1 ELSE 0 END) AS NUMERIC) / COUNT(*), 3) AS discount_usage_rate,

        ROUND(CAST(SUM(CASE WHEN o.package_protection_flag THEN 1 ELSE 0 END) AS NUMERIC) / COUNT(*), 3) AS package_protection_rate,

        ROUND(CAST(SUM(CASE WHEN o.return_flag THEN 1 ELSE 0 END) AS NUMERIC) / COUNT(*), 3) AS returns_rate

    FROM fact_orders o

    WHERE o.state IS NOT NULL AND o.state != ''

    GROUP BY o.state

    ORDER BY o.state;


-- ----------------------------------------------------------------------------
-- cohort_retention: retention rate by acquisition cohort and month offset.
-- Requires the cohort columns added to fact_orders in 02_derived_tables.sql.
-- ----------------------------------------------------------------------------
CREATE VIEW cohort_retention AS
WITH cohort_size AS (
    SELECT
        cohort_month,
        COUNT(DISTINCT(customer_id)) AS total_customers
    FROM fact_orders o
    WHERE cohort_index = 0
    GROUP BY cohort_month
),

retention AS (
    SELECT
        cohort_month,
        cohort_index,
        COUNT(DISTINCT(customer_id)) AS retained_customers
    FROM fact_orders
    GROUP BY cohort_month, cohort_index
)
SELECT
    r.cohort_month,
    r.cohort_index,
    r.retained_customers,
    cs.total_customers,
    ROUND((r.retained_customers * 1.0) / NULLIF(cs.total_customers, 0), 3) AS retention_rate
FROM retention r
JOIN cohort_size cs
    ON r.cohort_month = cs.cohort_month
ORDER BY r.cohort_month, r.cohort_index;
