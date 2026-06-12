"""Generate a synthetic sample of the Shopify orders export.

Produces a fully fake dataset that mirrors the schema and quirks of the real
`data/orders.csv` (which is NOT committed because it contains customer PII):

  * Same 79-column header, in the same order.
  * Line-item-level rows: a multi-product order spans several rows, and the
    order-level fields are populated only on the FIRST row of each order.
  * Generic product families / SKUs (anonymized as Product A-E), a plausible
    product mix, a few excluded items (package protection, gift card), and a
    ~12% repeat rate.

All names, emails, and addresses are randomly generated and do not correspond
to real people. Deterministic via a fixed seed so the output is reproducible.

Usage:  python scripts/make_sample_data.py
Output: data/sample/orders_sample.csv  (100 customers, ~115-125 orders)
"""

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

SEED = 42
N_CUSTOMERS = 100
REPEAT_RATE = 0.12  # share of customers who place more than one order
OUT = Path("data/sample/orders_sample.csv")

random.seed(SEED)

HEADER = [
    "Name", "Email", "Financial Status", "Paid at", "Fulfillment Status",
    "Fulfilled at", "Accepts Marketing", "Currency", "Subtotal", "Shipping",
    "Taxes", "Total", "Discount Code", "Discount Amount", "Shipping Method",
    "Created at", "Lineitem quantity", "Lineitem name", "Lineitem price",
    "Lineitem compare at price", "Lineitem sku", "Lineitem requires shipping",
    "Lineitem taxable", "Lineitem fulfillment status", "Billing Name",
    "Billing Street", "Billing Address1", "Billing Address2", "Billing Company",
    "Billing City", "Billing Zip", "Billing Province", "Billing Country",
    "Billing Phone", "Shipping Name", "Shipping Street", "Shipping Address1",
    "Shipping Address2", "Shipping Company", "Shipping City", "Shipping Zip",
    "Shipping Province", "Shipping Country", "Shipping Phone", "Notes",
    "Note Attributes", "Cancelled at", "Payment Method", "Payment Reference",
    "Refunded Amount", "Vendor", "Outstanding Balance", "Employee", "Location",
    "Device ID", "Id", "Tags", "Risk Level", "Source", "Lineitem discount",
    "Tax 1 Name", "Tax 1 Value", "Tax 2 Name", "Tax 2 Value", "Tax 3 Name",
    "Tax 3 Value", "Tax 4 Name", "Tax 4 Value", "Tax 5 Name", "Tax 5 Value",
    "Phone", "Receipt Number", "Duties", "Billing Province Name",
    "Shipping Province Name", "Payment ID", "Payment Terms Name",
    "Next Payment Due At", "Payment References",
]

FIRST_NAMES = [
    "Olivia", "Liam", "Emma", "Noah", "Ava", "Ethan", "Sophia", "Mason",
    "Isabella", "Logan", "Mia", "Lucas", "Amelia", "Jackson", "Harper",
    "Aiden", "Evelyn", "Elijah", "Abigail", "James", "Ella", "Benjamin",
    "Scarlett", "Henry", "Grace", "Sebastian", "Chloe", "Jack", "Lily",
    "Owen", "Nora", "Daniel", "Zoey", "Matthew", "Hazel", "Leah", "David",
    "Aria", "Carter", "Penelope",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark",
    "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen", "King",
    "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores",
]
EMAIL_DOMAINS = ["example.com", "example.net", "sample.org", "test-mail.com"]
STREET_NAMES = [
    "Maple Ave", "Oak St", "Cedar Ln", "Pine Rd", "Elm St", "Sunset Blvd",
    "Lakeview Dr", "Hillcrest Ave", "Washington St", "Park Ave", "2nd St",
    "Birch Way", "Willow Ct", "River Rd", "Highland Ave",
]

# (province code, province name, city) tuples weighted toward real US mix.
LOCATIONS = [
    ("CA", "California", "Los Angeles"), ("CA", "California", "San Diego"),
    ("NY", "New York", "Brooklyn"), ("NY", "New York", "Buffalo"),
    ("FL", "Florida", "Miami"), ("FL", "Florida", "Orlando"),
    ("TX", "Texas", "Austin"), ("TX", "Texas", "Houston"),
    ("NJ", "New Jersey", "Newark"), ("IL", "Illinois", "Chicago"),
    ("PA", "Pennsylvania", "Philadelphia"), ("OH", "Ohio", "Columbus"),
    ("WA", "Washington", "Seattle"), ("GA", "Georgia", "Atlanta"),
    ("MA", "Massachusetts", "Boston"), ("AZ", "Arizona", "Phoenix"),
]
LOCATION_WEIGHTS = [18, 10, 13, 4, 12, 6, 10, 7, 9, 7, 6, 5, 6, 6, 5, 5]

# Generic product catalogue (anonymized families Product A-E):
# (lineitem name, sku, unit price, weight). Weights drive the product mix.
PRODUCTS = [
    ("Product A - Variant 1", "PRODA-V1", 29.00, 18),
    ("Product A - Variant 2", "PRODA-V2", 29.00, 17),
    ("Product A - Variant 3", "PRODA-V3", 29.00, 7),
    ("Product A - Variant 4", "PRODA-V4", 29.00, 2),
    ("Product B - Variant 1", "PRODB-V1", 12.00, 7),
    ("Product B - Variant 2", "PRODB-V2", 12.00, 5),
    ("Product B - Variant 3", "PRODB-V3", 12.00, 3),
    ("Product B - Sample Pack", "PRODB-SAMPLE", 6.00, 2),
    ("Product C", "PRODC-15ML", 24.00, 14),
    ("Product D", "PRODD-10ML", 27.00, 12),
    ("Product E", "PRODE-01", 18.00, 2),
]
PRODUCT_WEIGHTS = [p[3] for p in PRODUCTS]

# Excluded / non-product items that show up in real exports.
PROTECTION_TIERS = [
    ("Package Protection - $0.98", "PP-1", 0.98),
    ("Package Protection - $2.50", "PP-2", 2.50),
    ("Package Protection - $2.88", "PP-3", 2.88),
    ("Package Protection - $3.25", "PP-4", 3.25),
]
GIFT_CARD = ("Gift Card", "", 25.00)

DISCOUNTS = ["", "", "", "", "PROMO10", "WELCOME10", "PROMO2023"]
SHIP_METHODS = ["Standard", "Ground Advantage", "Economy", "Priority Mail"]

DATE_START = datetime(2023, 1, 5)
DATE_END = datetime(2024, 12, 20)


def money(x):
    return f"{x:.2f}"


def rand_date():
    span = (DATE_END - DATE_START).days
    d = DATE_START + timedelta(
        days=random.randint(0, span),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59),
    )
    return d


def make_customers():
    customers = []
    used = set()
    for _ in range(N_CUSTOMERS):
        fn, ln = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
        tag = random.randint(1, 999)
        email = f"{fn.lower()}.{ln.lower()}{tag}@{random.choice(EMAIL_DOMAINS)}"
        while email in used:
            tag = random.randint(1, 9999)
            email = f"{fn.lower()}.{ln.lower()}{tag}@{random.choice(EMAIL_DOMAINS)}"
        used.add(email)
        loc = random.choices(LOCATIONS, weights=LOCATION_WEIGHTS, k=1)[0]
        customers.append({
            "name": f"{fn} {ln}",
            "email": email,
            "accepts": random.choices(["yes", "no"], weights=[78, 22])[0],
            "loc": loc,
            "street": f"{random.randint(10, 9999)} {random.choice(STREET_NAMES)}",
            "zip": f"{random.randint(10000, 99999)}",
            "phone": f"+1{random.randint(2000000000, 9999999999)}",
        })
    return customers


def assign_orders(customers):
    """Return a list of (customer, created_at).

    Mirrors the real store's shape: ~12% of customers repeat, and the repeaters
    follow a realistic decaying distribution: most place 2 orders, a few place
    3, and the rare outlier places 4+. Total order count is NOT pinned; it
    floats out of the distribution (typically ~115-125 for 100 customers).
    """
    events = [(c, rand_date()) for c in customers]  # everyone has 1 order
    n_repeaters = round(N_CUSTOMERS * REPEAT_RATE)
    repeaters = random.sample(customers, k=n_repeaters)
    # Total orders per repeater: mostly 2, a few 3, rare 4+.
    totals = random.choices([2, 3, 4], weights=[72, 22, 6], k=n_repeaters)
    for cust, total in zip(repeaters, totals):
        for _ in range(total - 1):  # extra orders beyond the first
            events.append((cust, rand_date()))
    events.sort(key=lambda e: e[1])
    return events


def build_lineitems():
    """Pick 1-3 products for an order; maybe add package protection / gift card."""
    n = random.choices([1, 2, 3], weights=[68, 24, 8], k=1)[0]
    chosen = random.choices(PRODUCTS, weights=PRODUCT_WEIGHTS, k=n)
    items = []
    for name, sku, price, _w in chosen:
        qty = random.choices([1, 2], weights=[88, 12], k=1)[0]
        items.append({"name": name, "sku": sku, "price": price, "qty": qty,
                      "ship": "true", "tax": "true", "vendor": "DTC Brand"})
    # ~30% of orders carry package protection (excluded downstream).
    if random.random() < 0.30:
        name, sku, price = random.choice(PROTECTION_TIERS)
        items.append({"name": name, "sku": sku, "price": price, "qty": 1,
                      "ship": "false", "tax": "false",
                      "vendor": "Package Protection Vendor"})
    # ~4% include a gift card (also excluded downstream).
    if random.random() < 0.04:
        name, sku, price = GIFT_CARD
        items.append({"name": name, "sku": sku, "price": price, "qty": 1,
                      "ship": "false", "tax": "false", "vendor": "DTC Brand"})
    return items


def order_row_blank():
    return {col: "" for col in HEADER}


def main():
    customers = make_customers()
    events = assign_orders(customers)

    rows = []
    order_seq = 2500
    for cust, created in events:
        order_seq += random.randint(1, 4)
        order_name = f"#{order_seq}"
        items = build_lineitems()

        product_subtotal = sum(i["price"] * i["qty"] for i in items
                               if i["vendor"] != "Package Protection Vendor")
        protection_subtotal = sum(i["price"] * i["qty"] for i in items
                                  if i["vendor"] == "Package Protection Vendor")
        discount_code = random.choice(DISCOUNTS)
        discount_amt = round(product_subtotal * 0.10, 2) if discount_code else 0.0
        subtotal = round(product_subtotal + protection_subtotal, 2)
        shipping = random.choices([0.0, 4.96, 5.36, 6.10],
                                  weights=[55, 18, 15, 12], k=1)[0]
        loc = cust["loc"]
        taxes = round((subtotal - discount_amt) * random.choice([0.0, 0.06, 0.0825]), 2)
        total = round(subtotal - discount_amt + shipping + taxes, 2)
        created_str = created.strftime("%Y-%m-%d %H:%M:%S -0500")

        for idx, item in enumerate(items):
            row = order_row_blank()
            # Line-item fields populated on every row.
            row["Name"] = order_name
            row["Lineitem quantity"] = str(item["qty"])
            row["Lineitem name"] = item["name"]
            row["Lineitem price"] = money(item["price"])
            row["Lineitem compare at price"] = ""
            row["Lineitem sku"] = item["sku"]
            row["Lineitem requires shipping"] = item["ship"]
            row["Lineitem taxable"] = item["tax"]
            row["Lineitem fulfillment status"] = "fulfilled"
            row["Lineitem discount"] = "0.00"
            row["Vendor"] = item["vendor"]
            row["Id"] = str(7000000000000 + order_seq)

            if idx == 0:
                # Order-level fields ONLY on the first row of the order.
                row["Email"] = cust["email"]
                row["Financial Status"] = "paid"
                row["Paid at"] = created_str
                row["Fulfillment Status"] = "fulfilled"
                row["Fulfilled at"] = (created + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S -0500")
                row["Accepts Marketing"] = cust["accepts"]
                row["Currency"] = "USD"
                row["Subtotal"] = money(subtotal)
                row["Shipping"] = money(shipping)
                row["Taxes"] = money(taxes)
                row["Total"] = money(total)
                row["Discount Code"] = discount_code
                row["Discount Amount"] = money(discount_amt)
                row["Shipping Method"] = random.choice(SHIP_METHODS)
                row["Created at"] = created_str
                row["Billing Name"] = cust["name"]
                row["Billing Street"] = cust["street"]
                row["Billing Address1"] = cust["street"]
                row["Billing City"] = loc[2]
                row["Billing Zip"] = cust["zip"]
                row["Billing Province"] = loc[0]
                row["Billing Province Name"] = loc[1]
                row["Billing Country"] = "US"
                row["Billing Phone"] = cust["phone"]
                row["Shipping Name"] = cust["name"]
                row["Shipping Street"] = cust["street"]
                row["Shipping Address1"] = cust["street"]
                row["Shipping City"] = loc[2]
                row["Shipping Zip"] = cust["zip"]
                row["Shipping Province"] = loc[0]
                row["Shipping Province Name"] = loc[1]
                row["Shipping Country"] = "US"
                row["Shipping Phone"] = cust["phone"]
                row["Payment Method"] = "Shopify Payments"
                row["Outstanding Balance"] = "0.00"
                row["Source"] = "web"
                row["Risk Level"] = "Low"
                row["Phone"] = cust["phone"]
            rows.append(row)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)

    n_orders = len({r["Name"] for r in rows})
    n_custs = len({c["email"] for c in customers})
    print(f"Wrote {OUT}")
    print(f"  rows (line items): {len(rows)}")
    print(f"  distinct orders:   {n_orders}")
    print(f"  distinct customers:{n_custs}")


if __name__ == "__main__":
    main()
