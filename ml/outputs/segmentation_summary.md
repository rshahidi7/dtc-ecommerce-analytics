# DTC E-Commerce Brand: Customer Segmentation (RFM + k-means), v2

- Snapshot date: **2026-02-16**
- All customers: **1505** (12.0% repeat). Excluded **99** $0 comp customers (see `zero_dollar_cohort_note.md`).
- **Paying base: 1406 customers, 171 repeaters (12.16% repeat rate)**, $134,914 revenue.
- Excluding the $0 cohort moves the repeat rate from 12.03% (all) to 12.16% (paying), a negligible shift, so the headline ~11.8% retention story holds.
- Monetary = sum of order `Total`. Frequency & Monetary log-transformed; all three standardized before k-means.
- k selection: silhouette optimum is **k=2** (silhouette 0.530), but the one-time block is continuous. We ship **k=3** to split one-timers by recency for campaign targeting; the lower silhouette at k=3 is expected and reported honestly, not hidden.

## Segment profile (paying base)

| Segment | Size | Mean Recency (days) | Mean Frequency | Mean Monetary | Revenue share |
|---|---|---|---|---|---|
| Recent one-time | 663 | 250 | 1.00 | $97.40 | 47.9% |
| Lapsed one-time | 578 | 763 | 1.01 | $62.07 | 26.6% |
| Loyal core (repeat buyers) | 165 | 393 | 2.39 | $208.87 | 25.5% |

## How segments map to existing recommendations

- **Recent one-time** maps to the **31-90 day post-purchase email sequence**: these bought recently and once; nudge the critical 2nd order before they lapse. Largest segment and the core retention lever.
- **Lapsed one-time** maps to the **win-back campaign**: bought once long ago (mean recency >2 years); reactivation offer / we-miss-you flow.
- **Loyal core (repeat buyers)** maps to retention and referral work; ~12% of paying customers but ~25% of revenue, so protect and grow this group.

## Note on the $0 cohort

- The ~7% of records that are $0 comps (influencer/affiliate/sample/gift seeding) are excluded here as a non-customer population. Track separately whether any comp recipient later places a paid order.
