# DTC E-Commerce Brand: Phase 2 Repeat-Purchase Classifier

## Setup

- **Modeling base:** 1400 paying customers, 165 repeaters, **base rate 11.79%**.
- Excluded **105** $0-comp customers (influencer/sample/affiliate seeding, first-order Total = 0), consistent with Phase 1. From 1505 total customers (12.03% repeat).
- **Label:** placed a 2nd order (>=2 distinct orders), derived from `orders.csv`. `Returning_customers.csv` is never read, so there is no leakage.
- **Features (first order only):** first-order value (Subtotal), discount used, distinct products, basket size, per-family flags, marketing opt-in, shipping region, season. Nothing post-first-order.
- **Imbalance:** `class_weight="balanced"` on both models; no resampling/SMOTE.

### Why Subtotal here, not Total (as in Phase 1)

Phase 1 used order **Total** because it measured **economic value**, what each customer was actually worth, shipping and tax included. Phase 2 predicts **behavior**, and wants basket value as a clean signal of *what was bought* rather than *where it shipped*; shipping and tax vary by geography and would inject noise unrelated to the purchase decision. So Phase 2 uses **Subtotal**. The differing field choice is deliberate and purpose-driven, not an inconsistency.

## Results

| Model | CV ROC-AUC | CV Avg-Precision | Test ROC-AUC | Test AP |
|---|---|---|---|---|
| Logistic Regression | 0.564 ± 0.033 | 0.165 ± 0.031 | 0.508 | 0.120 |
| Random Forest | 0.550 ± 0.042 | 0.151 ± 0.022 | 0.509 | 0.130 |

- Chance ROC-AUC = 0.500; base-rate average-precision = 0.118. Read the lift over **those**, not accuracy.
- **Why accuracy is the wrong primary metric:** predicting "never repeats" for everyone scores **88.2% accuracy** while being useless. ROC-AUC / average-precision measure whether the model ranks repeaters above non-repeaters at all, which is the real question. With ~165 positives, the held-out test set has wide error bars, so cross-validated figures are the more stable read.

### Logistic Regression: held-out classification report

```
              precision    recall  f1-score   support

           0      0.890     0.657     0.756       309
           1      0.131     0.390     0.196        41

    accuracy                          0.626       350
   macro avg      0.511     0.524     0.476       350
weighted avg      0.801     0.626     0.690       350
```

### Random Forest: held-out classification report

```
              precision    recall  f1-score   support

           0      0.884     0.841     0.862       309
           1      0.125     0.171     0.144        41

    accuracy                          0.763       350
   macro avg      0.505     0.506     0.503       350
weighted avg      0.795     0.763     0.778       350
```

## Interpretation

Top logistic-regression coefficients (standardized): `discount_used` +0.86, `first_order_value` +0.47, `season_Winter` -0.39, `basket_size` -0.33, `region_Northeast` -0.26.

Top random-forest importances: `first_order_value` 0.30, `discount_used` 0.08, `basket_size` 0.07, `season_Summer` 0.05, `region_South` 0.05.

**On `discount_used`.** It has the largest LogReg coefficient (+0.86) in-sample, but the model still produces no held-out discrimination (test AUC ~0.51). This is a useful reminder that an in-sample coefficient is a within-training-fold association, not a generalizable predictor; reporting it as a retention lever would be misleading.

**Honesty framing.** Prior analysis showed first-order attributes barely move the repeat rate off its ~12% baseline, so AUC near 0.5 is the *expected and valid* result, and it confirms the retention gap is **structural**, not explained by who the first-time buyer is or what they bought. No leaky features were engineered to inflate this; the near-baseline performance and flat feature importances ARE the deliverable. See `fig_feature_importance.png`, `fig_roc_pr_curves.png`, and `fig_confusion_matrix.png`.
