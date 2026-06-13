# SQL: Star Schema and Analytics Layer

PostgreSQL scripts that transform the raw Shopify exports into a star schema and
a set of analytical views, feeding the Power BI report. Product names and other
identifiers are anonymized (product families appear as Product A through E, the
shipping-protection add-on as Package Protection), consistent with the rest of
the repo.

## Files

| File | Purpose |
|---|---|
| `01_star_schema.sql` | Core warehouse: `fact_orders` (order grain), `dim_customers`, `dim_date`, `dim_location`, `dim_products`, and the `order_products` line-item bridge. |
| `02_derived_tables.sql` | Cohort augmentation of `fact_orders` (`cohort_month`, `cohort_index`), the `orders_cohort` table, and `inventory_health` (sell-through by category). |
| `03_views.sql` | Analytical views: `customer_kpis`, `monthly_revenue`, `order_behavior_metrics`, `geographic_performance`, and `cohort_retention`. |
| `04_validation.sql` | Read-only data-quality checks: grain, nulls, dimension uniqueness, referential integrity, and revenue reconciliation. |
| `05_reference_tables.sql` | Standalone table definitions for Shopify analytics exports (conversion funnel, sales by discount code, sales by channel). Independent of the star schema. |

## Recommended run order

1. Load the raw Shopify exports into the warehouse as the source tables the
   scripts read from: `orders`, `total_returns_by_order`,
   `products_by_percentage_sold`.
2. `01_star_schema.sql` to build the fact and dimension tables.
3. `02_derived_tables.sql` to add cohort columns and the derived tables.
4. `03_views.sql` to create the analytical views. The `cohort_retention` view
   depends on the cohort columns added in step 02, so it must run after it.
5. `04_validation.sql` any time after step 02 to sanity-check the build.

`05_reference_tables.sql` is independent and can be run whenever those Shopify
analytics exports are imported.

## Notes

- `fact_orders` and `dim_customers` are mutually referential: `fact_orders`
  joins `dim_customers` to pick up the `customer_id` surrogate key, while
  `dim_customers` is aggregated from `fact_orders` at the email grain. This
  reflects an iterative warehouse build. On a clean rebuild, materialize
  `dim_customers` from the order-email grain first, then build `fact_orders` so
  the surrogate key resolves.
- The scripts target PostgreSQL (window functions, `GENERATE_SERIES`,
  `DATE_TRUNC`, `AGE`, `ILIKE`, `::` casts).
- Anonymized product categories are assigned in `dim_products` via `ILIKE`
  pattern matching on the line-item name. On the real dataset those patterns
  match the real product strings; the committed scripts use the generic
  Product A through E names instead.
