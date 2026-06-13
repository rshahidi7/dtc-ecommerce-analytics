-- ============================================================================
-- 01_star_schema.sql
-- Core star schema for the analytics warehouse: the order-grain fact table,
-- the dimension tables, and the order-product bridge.
--
-- Source tables expected to exist already (raw Shopify exports loaded into the
-- warehouse): orders, total_returns_by_order.
--
-- Run this first. See README.md for the full run order and a note on the
-- fact_orders / dim_customers relationship.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- fact_orders: one row per order (order grain).
-- ----------------------------------------------------------------------------
CREATE TABLE fact_orders AS
    WITH returns_agg AS (
        SELECT
            "Order name",
            SUM("Total returns") AS return_amount
        FROM total_returns_by_order
        GROUP BY "Order name"
    )

    SELECT
        o."Name" AS order_id,

        c.customer_id,

        MAX(CAST(NULLIF(o."Paid at", '') AS DATE)) AS order_date_key,

        MAX(o."Total") AS order_revenue,

        SUM(CASE WHEN o."Lineitem name" ILIKE 'Package Protection%' THEN 0
                 WHEN o."Lineitem name" ILIKE '%commission%' THEN 0
                 WHEN o."Lineitem name" ILIKE '%Sample%' THEN 0
                 WHEN o."Lineitem name" ILIKE '%Stuff%' THEN 0
                 ELSE CAST(o."Lineitem quantity" AS int)
            END) AS product_count,

        SUM(CASE WHEN o."Lineitem name" ILIKE '%Sample%'
                  OR o."Lineitem name" ILIKE '%Stuff%'
                  OR o."Discount Code" ILIKE '%sample%'
                  OR o."Discount Code" ILIKE '%PR%'
                  OR o."Discount Code" ILIKE '%content%'
                  OR o."Discount Code" ILIKE '%campaign%'
                  OR o."Discount Code" ILIKE '%influencer%'
                  OR o."Discount Code" ILIKE '%affiliate%'
                 THEN CAST(o."Lineitem quantity" AS INT)
                 ELSE 0 END) AS sample_count,

        BOOL_OR(o."Lineitem name" ILIKE 'Package Protection%') AS package_protection_flag,

        CASE WHEN SUM(CAST(o."Discount Amount" AS NUMERIC)) > 0
             THEN TRUE
             ELSE FALSE
             END AS discount_flag,

        MAX(NULLIF(o."Discount Code", '')) AS discount_code,

        SUM(COALESCE(CAST(o."Discount Amount" AS numeric), 0)) AS discount_amount,

        CASE WHEN MAX(COALESCE(r.return_amount, 0)) < 0
             THEN TRUE
             ELSE FALSE
             END AS return_flag,

        ROUND(CAST(MAX(COALESCE(r.return_amount, 0)) AS numeric), 2) AS return_amount,

        MAX(o."Shipping Province") AS state

    FROM orders o

    LEFT JOIN returns_agg r
        ON o."Name" = r."Order name"

    LEFT JOIN dim_customers c
        ON LOWER(TRIM(o."Email")) = LOWER(TRIM(c.email))

    GROUP BY o."Name", c.customer_id;


-- ----------------------------------------------------------------------------
-- dim_customers: one row per customer (email grain), with lifetime aggregates.
-- ----------------------------------------------------------------------------
CREATE TABLE dim_customers AS
    SELECT
        ROW_NUMBER() OVER (ORDER BY email) AS customer_id,

        email,

        MIN(order_date_key) AS first_order_date,

        CAST(DATE_TRUNC('month', MIN(order_date_key)) AS date) AS cohort_month,

        CASE WHEN COUNT(order_id) > 1 THEN TRUE ELSE FALSE END AS repeat_customer,

        COUNT(order_id) AS num_orders,

        ROUND(CAST(SUM(order_revenue) AS numeric), 2) AS lifetime_revenue,

        ROUND(CAST(SUM(return_amount) AS numeric), 2) AS lifetime_returns,

        ROUND(CAST(SUM(order_revenue + return_amount) AS numeric), 2) AS net_lifetime_revenue,

        ROUND(CAST(SUM(order_revenue) / COUNT(order_id) AS numeric), 2) AS avg_order_revenue

    FROM fact_orders

    WHERE NULLIF(email, '') IS NOT NULL

    GROUP BY email;


-- ----------------------------------------------------------------------------
-- dim_date: one row per calendar day across the order date range.
-- ----------------------------------------------------------------------------
CREATE TABLE dim_date AS
    SELECT
        CAST(d AS DATE) AS date_key,
        EXTRACT(YEAR FROM d) AS year,
        EXTRACT(MONTH FROM d) AS month,
        TO_CHAR(d, 'Month') AS month_name,
        EXTRACT(QUARTER FROM d) AS quarter,
        EXTRACT(DAY FROM d) AS day,
        EXTRACT(DOW FROM d) AS day_of_week,
        TO_CHAR(d, 'Day') AS day_name
    FROM generate_series(
        (SELECT MIN(order_date_key) FROM fact_orders),
        (SELECT MAX(order_date_key) FROM fact_orders),
        INTERVAL '1 day'
    ) AS d;


-- ----------------------------------------------------------------------------
-- dim_location: one row per shipping state, with region / market rollups.
-- ----------------------------------------------------------------------------
CREATE TABLE dim_location AS
SELECT
    ROW_NUMBER() OVER (ORDER BY state) AS location_id,
    state,
    CASE
        -- Northeast
        WHEN state IN ('CT','ME','MA','NH','RI','VT') THEN 'New England'
        WHEN state IN ('NJ','NY','PA') THEN 'Mid-Atlantic'
        -- Midwest
        WHEN state IN ('IL','IN','MI','OH','WI') THEN 'East North Central'
        WHEN state IN ('IA','KS','MN','MO','NE','ND','SD') THEN 'West North Central'
        -- South
        WHEN state IN ('DE','FL','GA','MD','NC','SC','VA','WV','DC') THEN 'South Atlantic'
        WHEN state IN ('AL','KY','MS','TN') THEN 'East South Central'
        WHEN state IN ('AR','LA','OK','TX') THEN 'West South Central'
        -- West
        WHEN state IN ('AZ','CO','ID','MT','NV','NM','UT','WY') THEN 'Mountain'
        WHEN state IN ('AK','CA','HI','OR','WA') THEN 'Pacific'
        ELSE 'Other'
    END AS division,
    CASE
        WHEN state IN ('CT','ME','MA','NH','RI','VT','NJ','NY','PA') THEN 'Northeast'
        WHEN state IN ('IL','IN','MI','OH','WI','IA','KS','MN','MO','NE','ND','SD') THEN 'Midwest'
        WHEN state IN ('DE','FL','GA','MD','NC','SC','VA','WV','DC',
                       'AL','KY','MS','TN','AR','LA','OK','TX') THEN 'South'
        WHEN state IN ('AZ','CO','ID','MT','NV','NM','UT','WY',
                       'AK','CA','HI','OR','WA') THEN 'West'
        ELSE 'Other'
    END AS region,
    CASE
        -- Tier 1: largest economies / population (top states by GDP)
        WHEN state IN ('CA','TX','NY','FL') THEN 'Tier 1'
        -- Tier 2: mid-sized markets
        WHEN state IN ('IL','PA','OH','GA','NJ','WA','NC','VA','MA','MI','MD','CO','AZ','TN','IN','MN',
                       'WI','MO','OR','SC','AL','LA','KY','CT','UT','OK','NV') THEN 'Tier 2'
        -- Tier 3: smaller markets
        ELSE 'Tier 3'
    END AS market_tier,
    CASE
        WHEN state IN ('CA','OR','WA','NV','AZ','UT','ID','MT','WY','CO','NM','AK','HI') THEN 'Pacific/Mountain'
        WHEN state IN ('TX','OK','AR','LA','MS','AL','TN','KY') THEN 'South Central'
        WHEN state IN ('FL','GA','SC','NC','VA','WV','MD','DE','DC') THEN 'Southeast'
        WHEN state IN ('NY','NJ','PA','CT','RI','MA','VT','NH','ME') THEN 'Northeast Corridor'
        WHEN state IN ('OH','IN','IL','MI','WI','MN','IA','MO','ND','SD','NE','KS') THEN 'Midwest'
        ELSE 'Other'
    END AS shipping_zone
FROM (
    SELECT DISTINCT state
    FROM fact_orders
    WHERE state IS NOT NULL AND state != ''
) s;


-- ----------------------------------------------------------------------------
-- dim_products: one row per distinct line-item name, mapped to an anonymized
-- product category. Tax / fee / shipping junk rows are filtered out.
-- ----------------------------------------------------------------------------
CREATE TABLE dim_products AS
WITH base_products AS (
    SELECT DISTINCT
        "Lineitem name" AS raw_product_name
    FROM Orders
    WHERE "Lineitem name" IS NOT NULL
      AND "Lineitem name" != ''
      AND "Lineitem name" NOT ILIKE '%Tax%'
      AND "Lineitem name" NOT ILIKE '%Transit%'
      AND "Lineitem name" NOT ILIKE '%District%'
      AND "Lineitem name" NOT ILIKE '%Fee%'
      AND "Lineitem name" NOT ILIKE '%commission%'
      AND "Lineitem name" NOT ILIKE '%processing fee%'
      AND "Lineitem name" NOT ILIKE '%County%'
      AND "Lineitem name" NOT ILIKE '%City Tax%'
      AND "Lineitem name" NOT ILIKE '%State Tax%'
      AND "Lineitem name" NOT ILIKE '%Discount Cards%'
      AND "Lineitem name" !~ '^\d+\.?\d*$'
      AND "Lineitem name" != 'Low'
)

SELECT
    ROW_NUMBER() OVER (ORDER BY raw_product_name) AS product_id,
    raw_product_name,

    -- Product category (broad grouping; product families anonymized to A-E).
    CASE
        WHEN raw_product_name ILIKE '%Bundle%'             THEN 'Bundle'
        WHEN raw_product_name ILIKE '%Product A%'          THEN 'Product A'
        WHEN raw_product_name ILIKE '%Product B%'          THEN 'Product B'
        WHEN raw_product_name ILIKE '%Product C%'          THEN 'Product C'
        WHEN raw_product_name ILIKE '%Product D%'          THEN 'Product D'
        WHEN raw_product_name ILIKE '%Product E%'          THEN 'Product E'
        WHEN raw_product_name ILIKE '%Package Protection%' THEN 'Package Protection'
        WHEN raw_product_name ILIKE '%Gift Card%'          THEN 'Gift Card'
        WHEN raw_product_name ILIKE '%Sample%'             THEN 'Sample'
        ELSE 'Other'
    END AS product_category

FROM base_products;


-- ----------------------------------------------------------------------------
-- order_products: line-item bridge between orders and dim_products.
-- Package-protection line items are excluded.
-- ----------------------------------------------------------------------------
CREATE TABLE order_products AS
SELECT
    o."Name" AS order_id,
    p.product_id,
    p.raw_product_name,
    COALESCE(p.product_category, 'Other') AS product_category,
    o."Lineitem price",
    o."Lineitem quantity",
    (CAST(o."Lineitem price" AS numeric) * CAST(o."Lineitem quantity" AS numeric)) AS lineitem_revenue
FROM Orders o
LEFT JOIN dim_products p
    ON o."Lineitem name" = p.raw_product_name
WHERE p.product_category NOT ILIKE '%Package Protection%';
