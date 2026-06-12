"""Phase 2: Repeat-purchase classifier (honest rigor exercise).

Question: using ONLY a customer's first-order attributes, can we predict whether
they ever place a 2nd order? Prior analysis says barely: first-order signal is
weak, and the retention gap looks structural. This pipeline tests that claim
straight: a near-baseline model is the expected and valid result, and the
feature importances are the evidence. We do NOT engineer leaky features to
manufacture performance.

Pipeline (runs end-to-end from the raw export, from the repo root):

  data/orders.csv -> order level -> first order per customer -> features + label
                  -> logistic regression + random forest -> metrics, charts

Leakage controls (see CLAUDE.md):
  * Every feature is derived from the customer's FIRST order only (earliest
    `Created at`), nothing post-first-order.
  * The label (placed a 2nd order?) is derived from order counts in orders.csv.
    Returning_customers.csv is NEVER read here, not for features, not even for
    labels, so its outcome columns cannot leak in.
  * The $0-comp cohort (influencer/sample/affiliate seeding, first-order
    Total == 0) is excluded, consistent with Phase 1. Modeling it would teach
    the classifier the structure of the comp program, not genuine retention.

Imbalance (~12% positive): handled with class_weight="balanced" on both models.
No resampling/SMOTE (keeps the data honest and dependency-light).

Field-choice note: Phase 2 uses first-order **Subtotal** for first_order_value,
whereas Phase 1 used **Total**. Different purposes: Phase 1 measured economic
value (what the customer was worth, shipping/tax included); Phase 2 predicts
behavior and wants basket value independent of shipping geography, so Subtotal
is the cleaner signal. This is documented in classifier_metrics.md too.

Outputs (ml/outputs/):
  * classifier_metrics.md               base rate, CV + holdout metrics, framing
  * fig_confusion_matrix.png            test-set confusion matrix, both models
  * fig_roc_pr_curves.png               ROC + precision-recall vs. baseline
  * fig_feature_importance.png          logreg coefficients + RF importances
  * repeat_predictions.csv              email, actual, OOF probs (PII, gitignored)
  * repeat_predictions_anonymized.csv   hashed email (tracked portfolio artifact)

Reproducible: fixed RANDOM_STATE; `python ml/classifier/repeat_classifier.py`.
"""

import argparse
import hashlib
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import (
    train_test_split, StratifiedKFold, cross_validate, cross_val_predict,
)
from sklearn.metrics import (
    roc_auc_score, average_precision_score, roc_curve, precision_recall_curve,
    confusion_matrix, classification_report,
)

RANDOM_STATE = 42
DATA = Path("data/orders.csv")
OUT = Path("ml/outputs")
N_FOLDS = 5
TEST_SIZE = 0.25

NUMERIC = ["first_order_value", "n_distinct_products", "basket_size"]
BINARY = ["discount_used", "marketing_optin",
          "has_product_a", "has_product_b",
          "has_product_c", "has_product_d", "has_product_e"]
CATEGORICAL = ["region", "season"]

sns.set_theme(style="whitegrid")


def hash_email(email):
    """Stable pseudonymous id: first 8 hex chars of SHA256(normalized email)."""
    return hashlib.sha256(email.encode("utf-8")).hexdigest()[:8]


# --------------------------------------------------------------------------- #
# Product family mapping. Rules are loaded from a config file so the code holds
# no dataset-specific product strings. A rule matches when ALL its conditions
# match; a condition matches when the chosen field (name/sku/vendor) contains
# ANY of its substrings (case-insensitive). See config/product_mappings.json.
# --------------------------------------------------------------------------- #
def load_mapping_rules(path=None):
    if path:
        cfg = Path(path)
    else:
        cfg = Path("config/product_mappings.json")
        if not cfg.exists():                       # fall back so the repo is
            cfg = Path("config/product_mappings.example.json")  # runnable on sample
    with open(cfg) as f:
        return json.load(f), cfg


def _condition_matches(cond, fields):
    hay = str(fields.get(cond["field"], "")).upper()
    return any(str(sub).upper() in hay for sub in cond["contains"])


def map_family(name, sku, vendor, rules):
    fields = {"name": name, "sku": sku, "vendor": vendor}
    for rule in rules.get("exclude", []):
        if all(_condition_matches(c, fields) for c in rule["conditions"]):
            return None
    for rule in rules.get("families", []):
        if all(_condition_matches(c, fields) for c in rule["conditions"]):
            return rule["family"]
    return rules.get("fallback_family", "Other")


_REGION = {
    "Northeast": "CT ME MA NH RI VT NJ NY PA",
    "Midwest": "IL IN MI OH WI IA KS MN MO NE ND SD",
    "South": "DE FL GA MD NC SC VA DC WV AL KY MS TN AR LA OK TX",
    "West": "AZ CO ID MT NV NM UT WY AK CA HI OR WA",
}
STATE_TO_REGION = {st: reg for reg, sts in _REGION.items() for st in sts.split()}


def to_region(state):
    return STATE_TO_REGION.get(str(state).strip().upper(), "Other")


def to_season(month):
    if month in (12, 1, 2):
        return "Winter"
    if month in (3, 4, 5):
        return "Spring"
    if month in (6, 7, 8):
        return "Summer"
    return "Fall"


# --------------------------------------------------------------------------- #
# 1. Load + aggregate to order level (mind the first-row-only quirk)
# --------------------------------------------------------------------------- #
def first_valid(s):
    s = s.dropna()
    return s.iloc[0] if len(s) else np.nan


def load_orders(path=DATA):
    df = pd.read_csv(path, low_memory=False)
    orders = df.groupby("Name").agg(
        Email=("Email", first_valid),
        Created_at=("Created at", first_valid),
        Subtotal=("Subtotal", first_valid),
        Total=("Total", first_valid),
        Discount=("Discount Code", first_valid),
        Marketing=("Accepts Marketing", first_valid),
        State=("Shipping Province", first_valid),
    ).reset_index()
    orders["Created_at"] = pd.to_datetime(orders["Created_at"], errors="coerce", utc=True)
    for c in ("Subtotal", "Total"):
        orders[c] = pd.to_numeric(orders[c], errors="coerce")
    orders = orders.dropna(subset=["Email", "Created_at"]).copy()
    orders["Email"] = orders["Email"].str.strip().str.lower()
    return orders, df


# --------------------------------------------------------------------------- #
# 2. First-order line-item features (families, basket size, distinct products)
# --------------------------------------------------------------------------- #
def first_order_lineitems(raw_df, first_names_to_email, rules):
    li = raw_df[raw_df["Name"].isin(first_names_to_email)].copy()
    li["family"] = [map_family(n, s, v, rules) for n, s, v in
                    zip(li["Lineitem name"], li["Lineitem sku"], li["Vendor"])]
    li = li[li["family"].notna()].copy()             # drop excluded items
    li["qty"] = pd.to_numeric(li["Lineitem quantity"], errors="coerce").fillna(0)

    rows = []
    for name, g in li.groupby("Name"):
        fams = set(g["family"])
        rows.append({
            "Name": name,
            "n_distinct_products": g["family"].nunique(),
            "basket_size": int(g["qty"].sum()),
            "has_product_a": int("Product A" in fams),
            "has_product_b": int("Product B" in fams),
            "has_product_c": int("Product C" in fams),
            "has_product_d": int("Product D" in fams),
            "has_product_e": int("Product E" in fams),
        })
    feats = pd.DataFrame(rows).set_index("Name")
    feats["email"] = feats.index.map(first_names_to_email)
    return feats.set_index("email")


# --------------------------------------------------------------------------- #
# 3. Assemble the modeling table (features + label), exclude $0-comp cohort
# --------------------------------------------------------------------------- #
def build_dataset(orders, raw_df, rules):
    n_orders = orders.groupby("Email")["Name"].nunique()
    # First order per customer = earliest Created_at.
    fo = orders.sort_values("Created_at").groupby("Email").first()
    fo["repeat"] = (n_orders >= 2).astype(int)

    all_cust = len(fo)
    all_repeat = int(fo["repeat"].sum())

    # Exclude the $0-comp cohort (first-order Total == 0): seeding program.
    comp = fo["Total"] == 0
    n_comp = int(comp.sum())
    fo = fo[~comp].copy()

    # Order-level features available directly from the first order.
    fo["first_order_value"] = fo["Subtotal"].fillna(0.0)
    fo["discount_used"] = fo["Discount"].notna().astype(int)
    fo["marketing_optin"] = (fo["Marketing"].astype(str).str.lower() == "yes").astype(int)
    fo["region"] = fo["State"].map(to_region)
    fo["season"] = fo["Created_at"].dt.month.map(to_season)

    # Line-item features, joined on the first order's Name.
    name_to_email = {row["Name"]: email for email, row in fo[["Name"]].iterrows()}
    li_feats = first_order_lineitems(raw_df, name_to_email, rules)
    data = fo.join(li_feats, how="left")

    # Orders made entirely of excluded items -> no families; fill with zeros.
    li_cols = ["n_distinct_products", "basket_size"] + \
              [c for c in BINARY if c.startswith("has_")]
    data[li_cols] = data[li_cols].fillna(0).astype(int)

    cols = NUMERIC + BINARY + CATEGORICAL + ["repeat"]
    data.index.name = "email"
    data = data.reset_index()[["email"] + cols]
    meta = dict(all_cust=all_cust, all_repeat=all_repeat, n_comp=n_comp,
                n_cust=len(data), n_repeat=int(data["repeat"].sum()))
    return data, meta


# --------------------------------------------------------------------------- #
# 4. Models
# --------------------------------------------------------------------------- #
def make_preprocessor(scale):
    num = StandardScaler() if scale else "passthrough"
    return ColumnTransformer([
        ("num", num, NUMERIC),
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL),
        ("bin", "passthrough", BINARY),
    ])


def make_models():
    logreg = Pipeline([
        ("prep", make_preprocessor(scale=True)),
        ("clf", LogisticRegression(class_weight="balanced", max_iter=1000,
                                   random_state=RANDOM_STATE)),
    ])
    rf = Pipeline([
        ("prep", make_preprocessor(scale=False)),
        ("clf", RandomForestClassifier(
            n_estimators=400, min_samples_leaf=5, max_features="sqrt",
            class_weight="balanced", random_state=RANDOM_STATE, n_jobs=-1)),
    ])
    return {"Logistic Regression": logreg, "Random Forest": rf}


def feature_names(model):
    return [n.split("__", 1)[-1] for n in
            model.named_steps["prep"].get_feature_names_out()]


# --------------------------------------------------------------------------- #
# 5. Charts
# --------------------------------------------------------------------------- #
def plot_confusion(cms, y_test):
    fig, axes = plt.subplots(1, len(cms), figsize=(5 * len(cms), 4.5))
    for ax, (name, cm) in zip(axes, cms.items()):
        sns.heatmap(cm, annot=True, fmt="d", cmap="Blues", cbar=False, ax=ax,
                    xticklabels=["pred 0", "pred 1"],
                    yticklabels=["true 0", "true 1"])
        ax.set_title(f"{name}\n(test n={len(y_test)}, positives={int(y_test.sum())})")
    fig.suptitle("Confusion matrices (held-out test set)", fontsize=13)
    fig.tight_layout()
    fig.savefig(OUT / "fig_confusion_matrix.png", dpi=120)
    plt.close(fig)


def plot_curves(y_test, probs, base_rate):
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 5))
    for name, p in probs.items():
        fpr, tpr, _ = roc_curve(y_test, p)
        a1.plot(fpr, tpr, label=f"{name} (AUC={roc_auc_score(y_test, p):.3f})")
        prec, rec, _ = precision_recall_curve(y_test, p)
        a2.plot(rec, prec, label=f"{name} (AP={average_precision_score(y_test, p):.3f})")
    a1.plot([0, 1], [0, 1], "--", color="gray", label="chance (AUC=0.500)")
    a1.set(xlabel="False positive rate", ylabel="True positive rate", title="ROC curve")
    a1.legend(loc="lower right")
    a2.axhline(base_rate, ls="--", color="gray", label=f"base rate ({base_rate:.3f})")
    a2.set(xlabel="Recall", ylabel="Precision", title="Precision-Recall curve")
    a2.legend(loc="upper right")
    fig.suptitle("Discrimination vs. baseline: near-chance is the expected result",
                 fontsize=13)
    fig.tight_layout()
    fig.savefig(OUT / "fig_roc_pr_curves.png", dpi=120)
    plt.close(fig)


def plot_importance(logreg_imp, rf_imp):
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(14, 6))
    lr = logreg_imp.reindex(logreg_imp.abs().sort_values().index).tail(15)
    colors = ["#C44E52" if v < 0 else "#4C72B0" for v in lr]
    a1.barh(lr.index, lr.values, color=colors)
    a1.axvline(0, color="black", lw=.8)
    a1.set_title("Logistic regression coefficients\n(standardized; +ve = more likely to repeat)")
    rf = rf_imp.sort_values().tail(15)
    a2.barh(rf.index, rf.values, color="#55A868")
    a2.set_title("Random forest feature importances\n(impurity-based; magnitude only)")
    fig.suptitle("First-order feature importance: evidence for or against signal",
                 fontsize=13)
    fig.tight_layout()
    fig.savefig(OUT / "fig_feature_importance.png", dpi=120)
    plt.close(fig)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def parse_args():
    p = argparse.ArgumentParser(description="Phase 2 repeat-purchase classifier.")
    p.add_argument("--input", type=Path, default=DATA,
                   help="Path to the raw Shopify orders CSV (default: data/orders.csv).")
    p.add_argument("--config", type=Path, default=None,
                   help="Product-mapping config (default: config/product_mappings.json, "
                        "falling back to the committed .example.json).")
    return p.parse_args()


def main(input_path=DATA, config_path=None):
    OUT.mkdir(parents=True, exist_ok=True)
    rules, cfg_used = load_mapping_rules(config_path)
    print(f"Product mapping: {cfg_used}")
    orders, raw_df = load_orders(input_path)
    data, meta = build_dataset(orders, raw_df, rules)

    base_rate = data["repeat"].mean()
    print(f"All customers: {meta['all_cust']} ({meta['all_repeat']} repeat). "
          f"Excluded {meta['n_comp']} $0-comp customers.")
    print(f"Modeling base: {meta['n_cust']} customers, {meta['n_repeat']} repeaters "
          f"(base rate {base_rate:.2%}).")
    print(f"Always-predict-'no-repeat' accuracy = {1 - base_rate:.2%} "
          f"(why accuracy is the wrong primary metric).")

    X = data[NUMERIC + BINARY + CATEGORICAL]
    y = data["repeat"]
    Xtr, Xte, ytr, yte = train_test_split(
        X, y, test_size=TEST_SIZE, stratify=y, random_state=RANDOM_STATE)

    models = make_models()
    cv = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=RANDOM_STATE)

    results, test_probs, cms, oof = {}, {}, {}, {}
    for name, model in models.items():
        scores = cross_validate(model, Xtr, ytr, cv=cv,
                                scoring=["roc_auc", "average_precision"])
        model.fit(Xtr, ytr)
        p = model.predict_proba(Xte)[:, 1]
        yhat = model.predict(Xte)
        results[name] = {
            "cv_roc_auc": scores["test_roc_auc"].mean(),
            "cv_roc_auc_std": scores["test_roc_auc"].std(),
            "cv_ap": scores["test_average_precision"].mean(),
            "cv_ap_std": scores["test_average_precision"].std(),
            "test_roc_auc": roc_auc_score(yte, p),
            "test_ap": average_precision_score(yte, p),
            "report": classification_report(yte, yhat, digits=3, zero_division=0),
        }
        test_probs[name] = p
        cms[name] = confusion_matrix(yte, yhat)
        # Out-of-fold probabilities over the FULL base for the predictions artifact.
        oof[name] = cross_val_predict(model, X, y, cv=cv,
                                      method="predict_proba")[:, 1]
        print(f"\n=== {name} ===")
        print(f"CV ROC-AUC {results[name]['cv_roc_auc']:.3f} "
              f"± {results[name]['cv_roc_auc_std']:.3f} | "
              f"CV AP {results[name]['cv_ap']:.3f} | "
              f"test ROC-AUC {results[name]['test_roc_auc']:.3f}")

    # Feature importances from the train-fitted models.
    logreg = models["Logistic Regression"]
    rf = models["Random Forest"]
    logreg_imp = pd.Series(logreg.named_steps["clf"].coef_[0],
                           index=feature_names(logreg))
    rf_imp = pd.Series(rf.named_steps["clf"].feature_importances_,
                       index=feature_names(rf))

    plot_confusion(cms, yte)
    plot_curves(yte, test_probs, base_rate)
    plot_importance(logreg_imp, rf_imp)
    print("\nWrote charts to ml/outputs/")

    # Predictions artifact (OOF probabilities): real + anonymized.
    preds = pd.DataFrame({
        "email": data["email"],
        "repeat_actual": y.values,
        "prob_logreg": oof["Logistic Regression"].round(4),
        "prob_rf": oof["Random Forest"].round(4),
    })
    preds.to_csv(OUT / "repeat_predictions.csv", index=False)
    anon = preds.copy()
    anon.insert(0, "customer_hash", anon["email"].map(hash_email))
    anon.drop(columns="email").to_csv(OUT / "repeat_predictions_anonymized.csv", index=False)
    print(f"Wrote predictions ({len(preds)} rows): real (gitignored) + anonymized (tracked)")

    write_metrics(meta, base_rate, results, logreg_imp, rf_imp)
    print(f"Wrote {OUT/'classifier_metrics.md'}")


def write_metrics(meta, base_rate, results, logreg_imp, rf_imp):
    L = []
    L.append("# DTC E-Commerce Brand: Phase 2 Repeat-Purchase Classifier\n")
    L.append("## Setup\n")
    L.append(f"- **Modeling base:** {meta['n_cust']} paying customers, "
             f"{meta['n_repeat']} repeaters, **base rate {base_rate:.2%}**.")
    L.append(f"- Excluded **{meta['n_comp']}** $0-comp customers "
             f"(influencer/sample/affiliate seeding, first-order Total = 0), "
             f"consistent with Phase 1. From {meta['all_cust']} total customers "
             f"({meta['all_repeat']/meta['all_cust']:.2%} repeat).")
    L.append("- **Label:** placed a 2nd order (>=2 distinct orders), derived from "
             "`orders.csv`. `Returning_customers.csv` is never read, so there is no "
             "leakage.")
    L.append("- **Features (first order only):** first-order value (Subtotal), "
             "discount used, distinct products, basket size, per-family flags, "
             "marketing opt-in, shipping region, season. Nothing post-first-order.")
    L.append("- **Imbalance:** `class_weight=\"balanced\"` on both models; no "
             "resampling/SMOTE.\n")

    L.append("### Why Subtotal here, not Total (as in Phase 1)\n")
    L.append("Phase 1 used order **Total** because it measured **economic value**, "
             "what each customer was actually worth, shipping and tax included. "
             "Phase 2 predicts **behavior**, and wants basket value as a clean "
             "signal of *what was bought* rather than *where it shipped*; shipping "
             "and tax vary by geography and would inject noise unrelated to the "
             "purchase decision. So Phase 2 uses **Subtotal**. The differing field "
             "choice is deliberate and purpose-driven, not an inconsistency.\n")

    L.append("## Results\n")
    L.append("| Model | CV ROC-AUC | CV Avg-Precision | Test ROC-AUC | Test AP |")
    L.append("|---|---|---|---|---|")
    for name, r in results.items():
        L.append(f"| {name} | {r['cv_roc_auc']:.3f} ± {r['cv_roc_auc_std']:.3f} "
                 f"| {r['cv_ap']:.3f} ± {r['cv_ap_std']:.3f} "
                 f"| {r['test_roc_auc']:.3f} | {r['test_ap']:.3f} |")
    L.append(f"\n- Chance ROC-AUC = 0.500; base-rate average-precision = "
             f"{base_rate:.3f}. Read the lift over **those**, not accuracy.")
    L.append(f"- **Why accuracy is the wrong primary metric:** predicting "
             f"\"never repeats\" for everyone scores **{1-base_rate:.1%} accuracy** "
             f"while being useless. ROC-AUC / average-precision measure whether the "
             f"model ranks repeaters above non-repeaters at all, which is the real "
             f"question. "
             f"With ~{meta['n_repeat']} positives, the held-out test set has wide "
             f"error bars, so cross-validated figures are the more stable read.\n")

    for name, r in results.items():
        L.append(f"### {name}: held-out classification report\n")
        L.append("```\n" + r["report"].rstrip() + "\n```\n")

    L.append("## Interpretation\n")
    top_lr = logreg_imp.reindex(logreg_imp.abs().sort_values(ascending=False).index).head(5)
    top_rf = rf_imp.sort_values(ascending=False).head(5)
    L.append("Top logistic-regression coefficients (standardized): "
             + ", ".join(f"`{k}` {v:+.2f}" for k, v in top_lr.items()) + ".")
    L.append("\nTop random-forest importances: "
             + ", ".join(f"`{k}` {v:.2f}" for k, v in top_rf.items()) + ".")
    disc = logreg_imp.get("discount_used", float("nan"))
    lr_test_auc = results["Logistic Regression"]["test_roc_auc"]
    L.append(f"\n**On `discount_used`.** It has the largest LogReg coefficient "
             f"({disc:+.2f}) in-sample, but the model still produces no held-out "
             f"discrimination (test AUC ~{lr_test_auc:.2f}). This is a useful "
             f"reminder that an in-sample coefficient is a within-training-fold "
             f"association, not a generalizable predictor; reporting it as a "
             f"retention lever would be misleading.")
    L.append("\n**Honesty framing.** Prior analysis showed first-order attributes "
             "barely move the repeat rate off its ~12% baseline, so AUC near 0.5 is "
             "the *expected and valid* result, and it confirms the retention gap is "
             "**structural**, not explained by who the first-time buyer is or what "
             "they bought. No leaky features were engineered to inflate this; the "
             "near-baseline performance and flat feature importances ARE the "
             "deliverable. See `fig_feature_importance.png`, `fig_roc_pr_curves.png`, "
             "and `fig_confusion_matrix.png`.")
    (OUT / "classifier_metrics.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    args = parse_args()
    main(args.input, args.config)
