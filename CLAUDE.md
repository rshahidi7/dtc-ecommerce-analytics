# DTC E-Commerce Brand: Full Analytics Engagement

This repo holds the **entire analytics engagement for the client**, not just the machine-learning piece. It brings together the Power BI report, the SQL transformations behind it, the CEO presentation deck, and the Python ML extensions (customer segmentation and the repeat-purchase classifier) into one portfolio-quality project.

**How to use this doc:** Claude Code reads this `CLAUDE.md` automatically every session, so the project never has to be re-explained. The "Project Context" through "Constraints" sections describe the engagement as a whole; the "Phase 1 Kickoff" and "Phase 2" sections scope the ML workstream specifically.

**Anonymization note:** This is a public portfolio repo. The client's name, real product names, real vendor names, and real SKU codes do not appear anywhere in committed files. Product families are anonymized as Product A through Product E. The real string-to-family mapping lives only in `config/product_mappings.json`, which is gitignored and stays local.

---

## Project Context

I'm a data analyst building an end-to-end e-commerce analytics project on a Shopify store's data for the client, a direct-to-consumer e-commerce brand. The project serves two audiences:

1. **The company (CEO):** a real deliverable with actionable recommendations.
2. **Hiring managers:** a portfolio piece supporting my job search, so methodology rigor and honest evaluation matter as much as results.

The engagement spans the full analytics stack, and this repo collects all of it:

- **SQL transformations** (`sql/`): the queries that clean and reshape the raw Shopify exports into the modeled tables.
- **Power BI report** (`powerbi/`): star schema, dimension tables, geographic analysis; the primary BI deliverable (`.pbix`).
- **Presentation deck** (`deck/`): the findings I presented to the CEO.
- **ML extensions** (`ml/`): a small, honest data-science layer on top of the report: Phase 1 customer segmentation (RFM + k-means) and Phase 2 repeat-purchase classifier.

The headline finding from the report is that **retention is the core problem**: returning customers are about 2x more valuable, but only about 12% of customers ever place a second order, and prior analysis showed that *nothing about a customer's first purchase predicts whether they return*. The retention gap appears structural, not driven by customer quality or product mix.

The ML work adds rigor to that story rather than replacing it. Its outputs should remain importable back into Power BI as new tables/columns so the model and the report stay connected.

---

## Environment & Tooling

- Running locally on macOS (Apple Silicon). The ML workstream is pure Python and runs without Power BI; the `.pbix` report and deck are authored in their native tools (Power BI Desktop, slides) and committed here as deliverables.
- For the Python work, use a virtual environment. Standard stack: `pandas`, `numpy`, `scikit-learn`, `matplotlib`/`seaborn`. (`lifetimes` only if we reach the CLV stretch goal.)
- This is a portfolio repo that will go on GitHub, so: clean structure, a real `README.md`, reproducible scripts, and meaningful commits.

### Repository structure

```
data/            raw Shopify exports (gitignored, PII) + data/sample/ (tracked synthetic fixture)
config/          product_mappings.json (gitignored real mapping) + .example.json (tracked, generic)
scripts/         project-wide utilities (e.g. make_sample_data.py)
sql/             SQL transformations feeding the Power BI model
powerbi/         Power BI report (.pbix tracked; raw data sources under powerbi/ ignored)
deck/            CEO presentation deck
ml/
  segmentation/  Phase 1: RFM + k-means (segment_customers.py)
  classifier/    Phase 2: repeat-purchase classifier (repeat_classifier.py)
  outputs/       ML artifacts (real-email CSVs gitignored; charts/summaries/anonymized CSVs tracked)
CLAUDE.md, requirements.txt   at repo root
```

Run the pipelines from the repo root, for example:
`python ml/segmentation/segment_customers.py --input data/sample/orders_sample.csv`

---

## The Data

Put the CSV exports in a `data/` folder. The primary file is `orders.csv` (raw Shopify order export). Critical things to know about it:

**Structure quirk (important):** It's a *line-item-level* export. A multi-product order spans multiple rows, and the **order-level fields (`Email`, `Total`, `Subtotal`, `Discount Code`, `Accepts Marketing`, etc.) are only populated on the FIRST row of each order.** The continuation rows have those fields blank but repeat the order `Name`. When aggregating to order level, group by `Name` and take the first non-null value for order-level fields, and `min()` of `Created at` for the order date.

**Key columns:** `Name` (order ID, e.g. `#2791`), `Email` (customer ID), `Created at` (timestamp), `Total`, `Subtotal`, `Discount Code`, `Lineitem name`, `Lineitem sku`, `Lineitem price`, `Lineitem quantity`, `Shipping Province` / `Shipping Province Name` (state), `Billing Country`.

**Product family mapping:** the line-item names are messy variants. They are mapped to anonymized families and non-product items are excluded. The mapping rules live in `config/product_mappings.json` (gitignored) so no real product strings appear in code, with `config/product_mappings.example.json` providing a generic version that matches the synthetic sample. A rule matches when all its conditions match; a condition matches when the chosen field (name/sku/vendor) contains any of its substrings.

- Excluded entirely: package-protection line items (a third-party shipping add-on, not a client product) and gift cards.
- Product A: the higher-volume tape family (SKU-driven).
- Product B: the smaller tape family (SKU-driven).
- Product C, Product D, Product E: the cream, serum, and accessory families (name-driven).

**Other available files** (use only if helpful; `orders.csv` is the core): `Returning_customers.csv`, `All_sessions.csv`, `Conversion_rate_breakdown.csv`, `Orders_over_time.csv`, `Total_sales_by_product.csv`, `Sales_by_discount_codes.csv`.

**Data leakage warning:** `Returning_customers.csv` contains outcome columns: total number of orders, total amount spent, last order date. These ARE the prediction target. They must NEVER be used as model features for the Phase 2 classifier. Use them only to derive labels or to validate counts.

---

## Known Facts From Prior Analysis (for sanity-checking)

- About 1,505 unique customers; about 1,621 orders with a valid customer and date.
- About 178 repeat customers, roughly an **11.8% repeat rate**.
- Median first-order total around $63.81.
- First-order attributes (product, order value, discount use, basket size, email opt-in) all showed repeat rates within about 1 to 2 points of the 11.8% baseline, i.e. weak or no association with retention. Match these numbers when you reproduce the customer table; if you diverge materially, flag it.

---

## Phase 1 Kickoff

**Task: Customer segmentation via RFM + k-means.**

1. Load `orders.csv`, aggregate to order level (mind the first-row-only quirk), then to customer level keyed on `Email`.
2. Compute RFM features per customer:
   - **Recency:** days from the customer's last order to the dataset's max date.
   - **Frequency:** number of distinct orders.
   - **Monetary:** total spend (sum of order `Total`, or `Subtotal` if cleaner; state which and why).
3. Explore the distributions briefly (the data is heavily skewed toward one-time buyers, so handle that, e.g. log-transform monetary, and standardize before clustering).
4. Run k-means. Choose `k` deliberately using the elbow method and silhouette score; don't just pick a number. Report why.
5. Profile and **name** each cluster in business terms (e.g. "loyal core", "one-and-done", "lapsing high-value"), with a summary table of size, mean RFM, and share of revenue per cluster.
6. Outputs:
   - `ml/outputs/customer_segments.csv`: one row per customer (email, R, F, M, cluster id, cluster name). This is what I import into Power BI.
   - A few charts in `ml/outputs/` (cluster sizes, RFM profile per cluster, revenue share per cluster).
   - A short written summary of what the segments are and how they map to the existing recommendations (win-back campaign, 31 to 90 day post-purchase email sequence).

Start by exploring the data and confirming the customer count and the roughly 11.8% repeat rate before building anything.

---

## Phase 2

**Task: Repeat-purchase classifier, as an honest rigor exercise.**

- **Label:** binary, did the customer place a 2nd order?
- **Features:** ONLY first-order attributes (first product family, first-order value, discount used yes/no, distinct products in first order, marketing opt-in, shipping state/region, maybe month/season of first order). Absolutely no post-first-order information. No leakage from `Returning_customers.csv`.
- **Models:** logistic regression as an interpretable baseline, then a tree-based model (random forest or gradient boosting) for comparison.
- **Evaluation:** stratified train/test split (class imbalance about 12/88, so handle it with `class_weight`, and report precision/recall, ROC-AUC, and a confusion matrix, not just accuracy). Compare against the base rate.
- **Interpretation:** feature importance / coefficients.
- **Honesty framing:** prior analysis suggests first-order features barely predict retention, so a near-baseline model is the *expected and valid* result. Do NOT engineer leaky features or otherwise inflate performance to manufacture a "good" model. The finding, that first-order signal can't predict retention and the gap is structural, is the deliverable, and feature importance is the evidence. Report it straight.

---

## Constraints & Principles

- **Honesty over impressiveness.** This is a portfolio piece; a clean null result reported well beats a suspiciously perfect model. Never fabricate or inflate metrics.
- **No data leakage.** Features must only use information available at the time being predicted from.
- **Mind sample sizes.** With about 178 repeaters, any sub-segment gets thin fast. Flag any bucket under about 30 to 50 as directional, not conclusive.
- **Reproducibility.** Set random seeds. Keep the pipeline runnable end-to-end from the raw CSV.
- **Explain as you go.** I'm using this to learn the data-science workflow, so narrate your reasoning and decisions rather than just dumping code.
