-- ============================================================================
-- 05_reference_tables.sql
-- Standalone reference tables for analytics exports that come straight from
-- Shopify (conversion funnel, sales by discount code, sales by channel). These
-- are independent of the star schema and can be created at any time; populate
-- them by importing the corresponding Shopify CSV exports.
-- ============================================================================


-- 1. Conversion funnel data (monthly, from Shopify analytics).
CREATE TABLE conversion_rate_breakdown (
    month DATE PRIMARY KEY,
    sessions INTEGER,
    sessions_cart_additions INTEGER,
    sessions_reached_checkout INTEGER,
    sessions_completed_checkout INTEGER,
    conversion_rate NUMERIC(10,8)
);


-- 2. Sales by discount code (aggregated summary from Shopify).
CREATE TABLE sales_by_discount_code (
    discount_name VARCHAR(100),
    discount_method VARCHAR(50),
    discount_type VARCHAR(50),
    discount_class VARCHAR(50),
    applied_discounts NUMERIC(10,2),
    discounted_orders INTEGER,
    gross_sales NUMERIC(10,2),
    returns NUMERIC(10,2),
    net_sales NUMERIC(10,2),
    shipping_charges NUMERIC(10,2),
    taxes NUMERIC(10,2),
    total_sales NUMERIC(10,2)
);


-- 3. Sales by channel (aggregated summary from Shopify).
CREATE TABLE sales_by_sales_channel (
    sales_channel VARCHAR(100),
    orders INTEGER,
    gross_sales NUMERIC(10,2),
    discounts NUMERIC(10,2),
    returns NUMERIC(10,2),
    net_sales NUMERIC(10,2),
    shipping_charges NUMERIC(10,2),
    taxes NUMERIC(10,2),
    total_sales NUMERIC(10,2)
);
