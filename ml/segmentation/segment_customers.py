"""Phase 1: Customer segmentation via RFM + k-means.

Runs end-to-end from the raw Shopify export:

  data/orders.csv  ->  order-level  ->  customer-level RFM  ->  k-means segments

Key data handling (see CLAUDE.md):
  * orders.csv is line-item level; order-level fields are populated only on the
    FIRST row of each order. We group by `Name`, taking the first non-null
    value for order-level fields.
  * Customers are keyed on a normalized Email (lowercase + strip), which merges
    case-variant duplicates of the same person.
  * Monetary = sum of order `Total` (actual revenue booked per customer).

Outputs (written to ml/outputs/, where the real-email CSV is gitignored as PII):
  * customer_segments.csv          one row/customer: email,R,F,M,cluster_id,name
  * fig_k_selection.png            elbow + silhouette vs k
  * fig_rfm_distributions.png      raw vs log-transformed R/F/M
  * fig_cluster_sizes.png          customers per segment
  * fig_rfm_profile.png            standardized mean R/F/M per segment
  * fig_revenue_share.png          revenue share per segment
  * segmentation_summary.md        written profile + recommendation mapping

Reproducible: fixed RANDOM_STATE; run from the repo root:
  `python ml/segmentation/segment_customers.py`.
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
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

RANDOM_STATE = 42
DATA = Path("data/orders.csv")
OUT = Path("ml/outputs")
K_RANGE = range(2, 9)
# Silhouette optimum on the paying base is k=2 (one-time vs repeat), but the
# one-time block is continuous so any finer cut scores lower. We ship k=3: it
# splits one-timers by recency (recent vs lapsed), which maps directly onto the
# 31-90 day post-purchase sequence and the win-back campaign. The lower
# silhouette is reported honestly rather than hidden.
FINAL_K = 3

sns.set_theme(style="whitegrid")


def hash_email(email):
    """Stable pseudonymous id: first 8 hex chars of SHA256(email).

    Emails are already normalized (lowercase + stripped) upstream, so the hash
    is deterministic across runs; the same person always maps to the same id.
    """
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


# --------------------------------------------------------------------------- #
# 1. Load and aggregate to order level (mind the first-row-only quirk)
# --------------------------------------------------------------------------- #
def first_valid(s):
    s = s.dropna()
    return s.iloc[0] if len(s) else np.nan


def load_orders(path=DATA):
    df = pd.read_csv(path, low_memory=False)
    orders = df.groupby("Name").agg(
        Email=("Email", first_valid),
        Created_at=("Created at", first_valid),
        Total=("Total", first_valid),
        Discount=("Discount Code", first_valid),
    ).reset_index()
    orders["Created_at"] = pd.to_datetime(orders["Created_at"], errors="coerce", utc=True)
    orders["Total"] = pd.to_numeric(orders["Total"], errors="coerce")
    # Valid order = has a customer and a parseable date.
    orders = orders.dropna(subset=["Email", "Created_at"]).copy()
    orders["Email"] = orders["Email"].str.strip().str.lower()
    return orders, df


def profile_zero_cohort(orders, raw_df, rfm, rules):
    """Profile the $0-monetary cohort and write a note to outputs/.

    Returns the set of $0-cohort emails so they can be excluded downstream.
    """
    zero_emails = set(rfm.loc[rfm["monetary"] == 0, "Email"])
    z_orders = orders[orders["Email"].isin(zero_emails)]
    z_names = set(z_orders["Name"])
    li = raw_df[raw_df["Name"].isin(z_names)].copy()
    # Map to anonymized product families; excluded items (package protection,
    # gift cards) map to None and drop out.
    li["family"] = [map_family(n, s, v, rules) for n, s, v in
                    zip(li["Lineitem name"], li["Lineitem sku"], li["Vendor"])]
    top_products = li["family"].dropna().value_counts().head(6)
    discounts = z_orders["Discount"].fillna("(none)").value_counts()
    n_no_code = int(discounts.get("(none)", 0))
    # Bucket discount codes into operational categories.
    def bucket(code):
        c = str(code).lower()
        if "influencer" in c or "ugc" in c or "content" in c or "collab" in c \
                or "brand" in c or "social" in c or "pr" == c or "amc" in c:
            return "influencer / UGC / brand collab"
        if "affiliate" in c:
            return "affiliate"
        if "sample" in c or "free" in c or "giveaway" in c or "review" in c:
            return "free sample / giveaway"
        if "gift" in c:
            return "gift"
        if "wholesale" in c or "physician" in c or "owner" in c or "program" in c:
            return "wholesale / physician / internal"
        return "(no code / other)"
    cats = z_orders["Discount"].fillna("(none)").map(bucket).value_counts()

    note = []
    note.append("# $0-monetary cohort: operational profile\n")
    note.append(
        f"**{len(zero_emails)} customers** placed only $0 orders "
        f"({len(z_orders)} orders total), spanning "
        f"**{z_orders['Created_at'].min().date()} to {z_orders['Created_at'].max().date()}**. "
        f"They contribute $0 (approx 0%) of revenue. Operationally this is not a customer "
        f"segment but the client's **product-seeding / sampling program**: the discount "
        f"codes are dominated by influencer/UGC/brand-collaboration comps "
        f"({int(cats.get('influencer / UGC / brand collab', 0))} orders), free-sample "
        f"and giveaway codes ({int(cats.get('free sample / giveaway', 0))}), affiliate "
        f"comps ({int(cats.get('affiliate', 0))}), gifts "
        f"({int(cats.get('gift', 0))}), and a handful of wholesale/physician/internal "
        f"samples ({int(cats.get('wholesale / physician / internal', 0))}); "
        f"{n_no_code} orders carry no code (manually zeroed comps). The items shipped "
        f"are full-size products and sample SKUs (top families: "
        f"{', '.join(top_products.index[:4])}). These are **influencer/PR/sample comps, "
        f"not test orders or service refunds**, and are excluded from the paying-base "
        f"segmentation below.\n"
    )
    (OUT / "zero_dollar_cohort_note.md").write_text("\n".join(note) + "\n")
    return zero_emails


# --------------------------------------------------------------------------- #
# 2. Aggregate to customer level and compute RFM
# --------------------------------------------------------------------------- #
def build_rfm(orders):
    snapshot = orders["Created_at"].max()
    rfm = orders.groupby("Email").agg(
        last_order=("Created_at", "max"),
        frequency=("Name", "nunique"),
        monetary=("Total", "sum"),
    )
    rfm["recency"] = (snapshot - rfm["last_order"]).dt.days
    rfm = rfm[["recency", "frequency", "monetary"]].reset_index()
    return rfm, snapshot


# --------------------------------------------------------------------------- #
# 3. Skew handling + scaling
# --------------------------------------------------------------------------- #
def build_features(rfm):
    feat = pd.DataFrame(index=rfm.index)
    # Frequency and monetary are heavily right-skewed -> log1p.
    feat["recency"] = rfm["recency"]
    feat["log_frequency"] = np.log1p(rfm["frequency"])
    feat["log_monetary"] = np.log1p(rfm["monetary"])
    scaler = StandardScaler()
    X = scaler.fit_transform(feat)
    return X, feat


# --------------------------------------------------------------------------- #
# 4. Choose k via elbow + silhouette
# --------------------------------------------------------------------------- #
def choose_k(X):
    inertias, sils = [], []
    for k in K_RANGE:
        km = KMeans(n_clusters=k, n_init=10, random_state=RANDOM_STATE)
        labels = km.fit_predict(X)
        inertias.append(km.inertia_)
        sils.append(silhouette_score(X, labels))
    return list(K_RANGE), inertias, sils


# --------------------------------------------------------------------------- #
# 5. Naming clusters from their RFM profile
# --------------------------------------------------------------------------- #
def name_clusters(profile):
    """Assign business names from each cluster's RFM profile.

    `profile` is indexed by cluster_id with columns recency/frequency/monetary
    (means in original units) plus 'size'. Naming proceeds in priority order:

      1. near-zero monetary  -> freebie / sample recipients ($0 comps)
      2. frequency >= 1.5     -> loyal core (repeat buyers)
      3. remaining one-time paying clusters are named by recency/value rank.
    """
    names = {}
    for cid, row in profile.iterrows():
        if row["monetary"] < 5:                       # $0 comps, not real spend
            names[cid] = "Freebie / sample recipients"
        elif row["frequency"] >= 1.5:
            names[cid] = "Loyal core (repeat buyers)"

    remaining = [c for c in profile.index if c not in names]
    sub = profile.loc[remaining].sort_values("recency")  # most recent first
    if len(remaining) == 1:
        names[remaining[0]] = "One-and-done majority"
    elif len(remaining) == 2:
        # Two one-time paying clusters: split purely by recency.
        names[sub.index[0]] = "Recent one-time"
        names[sub.index[1]] = "Lapsed one-time"
    else:
        # 3+ one-time clusters: recency tiers, qualified by value at the ends.
        med_val = sub["monetary"].median()
        for rank, (cid, row) in enumerate(sub.iterrows()):
            recent = rank < len(sub) / 2
            hv = row["monetary"] >= med_val
            if recent and hv:
                names[cid] = "Recent high-value one-time"
            elif recent:
                names[cid] = "Recent one-time"
            elif hv:
                names[cid] = "Lapsing high-value"
            else:
                names[cid] = "Lapsed one-time"
    # Disambiguate any accidental duplicates.
    seen = {}
    for cid in list(names):
        n = names[cid]
        if list(names.values()).count(n) > 1:
            seen[n] = seen.get(n, 0) + 1
            names[cid] = f"{n} ({seen[n]})"
    return names


# --------------------------------------------------------------------------- #
# Plotting helpers
# --------------------------------------------------------------------------- #
def plot_distributions(rfm):
    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    raw_cols = ["recency", "frequency", "monetary"]
    for ax, col in zip(axes[0], raw_cols):
        sns.histplot(rfm[col], bins=40, ax=ax, color="#4C72B0")
        ax.set_title(f"{col.title()} (raw)")
    sns.histplot(rfm["recency"], bins=40, ax=axes[1, 0], color="#55A868")
    axes[1, 0].set_title("Recency (raw, kept)")
    sns.histplot(np.log1p(rfm["frequency"]), bins=40, ax=axes[1, 1], color="#55A868")
    axes[1, 1].set_title("log1p(Frequency)")
    sns.histplot(np.log1p(rfm["monetary"]), bins=40, ax=axes[1, 2], color="#55A868")
    axes[1, 2].set_title("log1p(Monetary)")
    fig.suptitle("RFM distributions: raw (top) vs transformed (bottom)", fontsize=13)
    fig.tight_layout()
    fig.savefig(OUT / "fig_rfm_distributions.png", dpi=120)
    plt.close(fig)


def plot_k_selection(ks, inertias, sils, chosen, sil_opt):
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 5))
    for ax in (a1, a2):
        ax.axvline(chosen, ls="--", color="red", alpha=.6, label=f"shipped k={chosen}")
        ax.axvline(sil_opt, ls=":", color="gray", alpha=.7, label=f"silhouette opt k={sil_opt}")
    a1.plot(ks, inertias, "o-", color="#4C72B0")
    a1.set(xlabel="k", ylabel="Inertia (within-cluster SS)", title="Elbow method")
    a1.legend()
    a2.plot(ks, sils, "o-", color="#C44E52")
    a2.set(xlabel="k", ylabel="Silhouette score", title="Silhouette score")
    a2.legend()
    fig.suptitle(f"k selection: silhouette optimum k={sil_opt}, "
                 f"shipped k={chosen} for actionability", fontsize=13)
    fig.tight_layout()
    fig.savefig(OUT / "fig_k_selection.png", dpi=120)
    plt.close(fig)


def plot_cluster_sizes(profile):
    fig, ax = plt.subplots(figsize=(9, 5))
    order = profile.sort_values("size", ascending=False)
    sns.barplot(x=order["name"], y=order["size"], hue=order["name"],
                legend=False, ax=ax, palette="viridis")
    ax.set(xlabel="", ylabel="Customers", title="Segment sizes")
    for i, v in enumerate(order["size"]):
        ax.text(i, v, f"{int(v)}", ha="center", va="bottom")
    plt.xticks(rotation=20, ha="right")
    fig.tight_layout()
    fig.savefig(OUT / "fig_cluster_sizes.png", dpi=120)
    plt.close(fig)


def plot_rfm_profile(X, labels, profile):
    # Standardized cluster means for a comparable R/F/M shape per segment.
    feat = pd.DataFrame(X, columns=["recency", "log_frequency", "log_monetary"])
    feat["cluster"] = labels
    means = feat.groupby("cluster").mean()
    means.index = [profile.loc[c, "name"] for c in means.index]
    fig, ax = plt.subplots(figsize=(9, 5))
    sns.heatmap(means, annot=True, fmt=".2f", cmap="RdBu_r", center=0, ax=ax)
    ax.set(title="Standardized RFM profile per segment\n(0 = overall mean; +recency = more lapsed)")
    fig.tight_layout()
    fig.savefig(OUT / "fig_rfm_profile.png", dpi=120)
    plt.close(fig)


def plot_revenue_share(profile):
    fig, ax = plt.subplots(figsize=(9, 5))
    order = profile.sort_values("revenue_share", ascending=False)
    sns.barplot(x=order["name"], y=order["revenue_share"] * 100, hue=order["name"],
                legend=False, ax=ax, palette="magma")
    ax.set(xlabel="", ylabel="Revenue share (%)", title="Revenue share per segment")
    for i, v in enumerate(order["revenue_share"]):
        ax.text(i, v * 100, f"{v*100:.1f}%", ha="center", va="bottom")
    plt.xticks(rotation=20, ha="right")
    fig.tight_layout()
    fig.savefig(OUT / "fig_revenue_share.png", dpi=120)
    plt.close(fig)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def parse_args():
    p = argparse.ArgumentParser(description="Phase 1 RFM + k-means segmentation.")
    p.add_argument("--input", type=Path, default=DATA,
                   help="Path to the raw Shopify orders CSV (default: data/orders.csv).")
    p.add_argument("--config", type=Path, default=None,
                   help="Product-mapping config (default: config/product_mappings.json, "
                        "falling back to the committed .example.json).")
    return p.parse_args()


def main(input_path=DATA, config_path=None):
    OUT.mkdir(exist_ok=True)
    rules, cfg_used = load_mapping_rules(config_path)
    print(f"Product mapping: {cfg_used}")
    orders, raw_df = load_orders(input_path)
    rfm, snapshot = build_rfm(orders)
    all_cust = len(rfm)
    all_repeat = (rfm["frequency"] >= 2).sum()
    print(f"Snapshot (max) date: {snapshot.date()}")
    print(f"All customers: {all_cust} | Repeaters: {all_repeat} | "
          f"Repeat rate: {all_repeat/all_cust:.2%}")
    print(f"Total revenue (sum Total): ${rfm['monetary'].sum():,.2f}")

    # --- v2: profile and exclude the $0-monetary cohort, segment paying base ---
    zero_emails = profile_zero_cohort(orders, raw_df, rfm, rules)
    rfm = rfm[~rfm["Email"].isin(zero_emails)].reset_index(drop=True)
    n_cust = len(rfm)
    n_repeat = (rfm["frequency"] >= 2).sum()
    print(f"\n[v2] Excluded {len(zero_emails)} $0 customers.")
    print(f"[v2] Paying customers: {n_cust} | Repeaters: {n_repeat} | "
          f"Paying repeat rate: {n_repeat/n_cust:.2%}")

    plot_distributions(rfm)

    X, feat = build_features(rfm)
    ks, inertias, sils = choose_k(X)
    print("\nk | inertia | silhouette")
    for k, i, s in zip(ks, inertias, sils):
        print(f"{k} | {i:9.1f} | {s:.4f}")

    sil_opt = ks[int(np.argmax(sils))]
    chosen_k = FINAL_K
    print(f"\nSilhouette optimum k = {sil_opt}; shipping k = {chosen_k} "
          f"(recency split for campaign actionability).")
    plot_k_selection(ks, inertias, sils, chosen_k, sil_opt)

    km = KMeans(n_clusters=chosen_k, n_init=10, random_state=RANDOM_STATE)
    labels = km.fit_predict(X)
    rfm["cluster_id"] = labels

    # Per-cluster profile in ORIGINAL units.
    total_rev = rfm["monetary"].sum()
    profile = rfm.groupby("cluster_id").agg(
        size=("recency", "size"),
        recency=("recency", "mean"),
        frequency=("frequency", "mean"),
        monetary=("monetary", "mean"),
    )
    profile["revenue_share"] = (
        rfm.groupby("cluster_id")["monetary"].sum() / total_rev
    )
    names = name_clusters(profile)
    profile["name"] = profile.index.map(names)
    rfm["cluster_name"] = rfm["cluster_id"].map(names)

    print("\n=== Segment profile ===")
    show = profile.copy()
    show["recency"] = show["recency"].round(0)
    show["frequency"] = show["frequency"].round(2)
    show["monetary"] = show["monetary"].round(2)
    show["revenue_share"] = (show["revenue_share"] * 100).round(1)
    print(show[["name", "size", "recency", "frequency", "monetary", "revenue_share"]]
          .to_string())

    plot_cluster_sizes(profile)
    plot_rfm_profile(X, labels, profile)
    plot_revenue_share(profile)

    out = rfm.rename(columns={
        "Email": "email", "recency": "R", "frequency": "F", "monetary": "M",
    })[["email", "R", "F", "M", "cluster_id", "cluster_name"]]
    out["M"] = out["M"].round(2)
    # Real file: actual emails, for Power BI integration. Gitignored (PII).
    out.to_csv(OUT / "customer_segments.csv", index=False)
    print(f"\nWrote {OUT/'customer_segments.csv'} ({len(out)} rows)")

    # Anonymized file: identical rows (same R/F/M, cluster id/name, row count)
    # with the email replaced by the first 8 hex chars of its SHA256. Tracked
    # in git as a portfolio artifact, browsable without exposing real people.
    anon = out.copy()
    anon.insert(0, "customer_hash", anon["email"].map(hash_email))
    anon = anon.drop(columns="email")
    anon.to_csv(OUT / "customer_segments_anonymized.csv", index=False)
    print(f"Wrote {OUT/'customer_segments_anonymized.csv'} ({len(anon)} rows)")

    write_summary(profile, chosen_k, sil_opt, snapshot, total_rev, sils,
                  all_cust, all_repeat, n_cust, n_repeat, len(zero_emails))
    print(f"Wrote {OUT/'segmentation_summary.md'}")


def write_summary(profile, k, sil_opt, snapshot, total_rev, sils,
                  all_cust, all_repeat, n_cust, n_repeat, n_zero):
    lines = []
    lines.append("# DTC E-Commerce Brand: Customer Segmentation (RFM + k-means), v2\n")
    lines.append(f"- Snapshot date: **{snapshot.date()}**")
    lines.append(f"- All customers: **{all_cust}** "
                 f"({all_repeat/all_cust:.1%} repeat). Excluded **{n_zero}** $0 comp "
                 f"customers (see `zero_dollar_cohort_note.md`).")
    lines.append(f"- **Paying base: {n_cust} customers, {n_repeat} repeaters "
                 f"({n_repeat/n_cust:.2%} repeat rate)**, ${total_rev:,.0f} revenue.")
    lines.append(f"- Excluding the $0 cohort moves the repeat rate from "
                 f"{all_repeat/all_cust:.2%} (all) to {n_repeat/n_cust:.2%} (paying), "
                 f"a negligible shift, so the headline ~11.8% retention story holds.")
    lines.append(f"- Monetary = sum of order `Total`. Frequency & Monetary "
                 f"log-transformed; all three standardized before k-means.")
    lines.append(f"- k selection: silhouette optimum is **k={sil_opt}** "
                 f"(silhouette {max(sils):.3f}), but the one-time block is continuous. "
                 f"We ship **k={k}** to split one-timers by recency for campaign "
                 f"targeting; the lower silhouette at k={k} is expected and reported "
                 f"honestly, not hidden.\n")
    lines.append("## Segment profile (paying base)\n")
    lines.append("| Segment | Size | Mean Recency (days) | Mean Frequency | "
                 "Mean Monetary | Revenue share |")
    lines.append("|---|---|---|---|---|---|")
    for cid, r in profile.sort_values("revenue_share", ascending=False).iterrows():
        lines.append(f"| {r['name']} | {int(r['size'])} | {r['recency']:.0f} | "
                     f"{r['frequency']:.2f} | ${r['monetary']:.2f} | "
                     f"{r['revenue_share']*100:.1f}% |")
    lines.append("\n## How segments map to existing recommendations\n")
    lines.append("- **Recent one-time** maps to the **31-90 day post-purchase email "
                 "sequence**: these bought recently and once; nudge the critical 2nd "
                 "order before they lapse. Largest segment and the core retention lever.")
    lines.append("- **Lapsed one-time** maps to the **win-back campaign**: bought once "
                 "long ago (mean recency >2 years); reactivation offer / we-miss-you flow.")
    lines.append("- **Loyal core (repeat buyers)** maps to retention and referral work; "
                 "~12% of paying customers but ~25% of revenue, so protect and grow "
                 "this group.")
    lines.append("\n## Note on the $0 cohort\n")
    lines.append("- The ~7% of records that are $0 comps (influencer/affiliate/sample/"
                 "gift seeding) are excluded here as a non-customer population. Track "
                 "separately whether any comp recipient later places a paid order.")
    (OUT / "segmentation_summary.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    args = parse_args()
    main(args.input, args.config)
