"""Generate reproducible synthetic e-commerce data and load it into the database in DATABASE_URL.

WARNING: loading deletes all existing rows in the six tables before inserting the new data.

Run from the project root:
    .venv/bin/python database/seed.py --dry-run   # generate and print a summary; no database access
    .venv/bin/python database/seed.py             # generate and load into the database
"""

import argparse
import random
import re
from collections import Counter
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

import psycopg
from faker import Faker

from apply_schema import DatabaseSettings, connect

# --- Reproducibility and size ----------------------------------------------------------

SEED = 42

# Fixed window (about two years) so every run produces exactly the same dataset.
# DATASET_END_DATE is the dataset's effective "today": the last possible order/payment date,
# and the reference date for order ages and historical prices (see PROJECT.md).
START_DATE = date(2024, 10, 1)
DATASET_END_DATE = date(2026, 9, 30)
TOTAL_DAYS = (DATASET_END_DATE - START_DATE).days + 1

NUM_CUSTOMERS = 1000
# Customers sign up from this many days before START_DATE until 60 days before DATASET_END_DATE.
# The growing customer base is what drives order growth (~1.5x from start to end).
SIGNUP_LEAD_DAYS = 600
PRODUCTS_PER_CATEGORY = 12  # 10 categories -> 120 products
NUM_ORDERS = 5000

# --- Reference data --------------------------------------------------------------------

# (city, state, relative share of customers)
CITIES = [
    ("Mumbai", "Maharashtra", 10), ("Delhi", "Delhi", 10), ("Bengaluru", "Karnataka", 9),
    ("Hyderabad", "Telangana", 7), ("Chennai", "Tamil Nadu", 6), ("Kolkata", "West Bengal", 6),
    ("Pune", "Maharashtra", 6), ("Ahmedabad", "Gujarat", 5), ("Jaipur", "Rajasthan", 4),
    ("Gurugram", "Haryana", 3), ("Lucknow", "Uttar Pradesh", 3), ("Surat", "Gujarat", 3),
    ("Kochi", "Kerala", 3), ("Noida", "Uttar Pradesh", 2), ("Chandigarh", "Chandigarh", 2),
    ("Indore", "Madhya Pradesh", 2), ("Bhopal", "Madhya Pradesh", 2), ("Nagpur", "Maharashtra", 2),
    ("Coimbatore", "Tamil Nadu", 2), ("Patna", "Bihar", 2), ("Bhubaneswar", "Odisha", 2),
    ("Guwahati", "Assam", 2), ("Visakhapatnam", "Andhra Pradesh", 2), ("Vadodara", "Gujarat", 1),
    ("Mysuru", "Karnataka", 1), ("Thiruvananthapuram", "Kerala", 1), ("Dehradun", "Uttarakhand", 1),
]

# Reserved example domains (RFC 2606), so no generated email can belong to a real person.
EMAIL_DOMAINS = ["example.com", "example.net", "example.org"]

# Invented brand names used to build product names.
BRANDS = ["Arkivo", "Nuvaro", "Trivani", "Kalvex", "Lumora", "Orvexa", "Sanvika", "Tezari", "Mirako", "Koshra"]


class CategorySpec(NamedTuple):
    name: str
    min_price: int  # INR
    max_price: int  # INR
    min_margin: float  # (price - cost) / price
    max_margin: float
    popularity: int  # relative share of items sold
    items: list[str]


CATEGORIES = [
    CategorySpec("Electronics", 1999, 39999, 0.08, 0.20, 9,
                 ["Smart TV", "Laptop", "Tablet", "Smartwatch", "Bluetooth Speaker", "Soundbar",
                  "Wireless Headphones", "Monitor", "Smartphone", "Action Camera"]),
    CategorySpec("Mobile Accessories", 199, 3999, 0.35, 0.55, 14,
                 ["Phone Case", "Screen Protector", "Power Bank", "Fast Charger", "USB-C Cable",
                  "Wireless Earbuds", "Car Mount", "Selfie Stick"]),
    CategorySpec("Apparel", 299, 4999, 0.40, 0.60, 18,
                 ["Cotton T-Shirt", "Denim Jeans", "Kurta", "Saree", "Hoodie", "Formal Shirt",
                  "Track Pants", "Jacket", "Leggings"]),
    CategorySpec("Footwear", 499, 7999, 0.35, 0.55, 9,
                 ["Running Shoes", "Sneakers", "Sandals", "Formal Shoes", "Flip Flops",
                  "Kolhapuri Chappals", "Sports Shoes"]),
    CategorySpec("Home & Kitchen", 299, 14999, 0.25, 0.45, 12,
                 ["Pressure Cooker", "Mixer Grinder", "Non-Stick Pan", "Bedsheet Set", "Water Bottle",
                  "Dinner Set", "Air Fryer", "Storage Containers", "Ceiling Fan"]),
    CategorySpec("Beauty & Personal Care", 149, 2999, 0.35, 0.55, 11,
                 ["Face Wash", "Moisturiser", "Shampoo", "Hair Dryer", "Beard Trimmer", "Sunscreen",
                  "Lipstick", "Perfume"]),
    CategorySpec("Books", 149, 1499, 0.20, 0.35, 7,
                 ["Novel", "Cookbook", "Self-Help Book", "Exam Guide", "Children's Storybook",
                  "Biography", "Comic Collection"]),
    CategorySpec("Sports & Fitness", 299, 19999, 0.25, 0.40, 6,
                 ["Yoga Mat", "Dumbbell Set", "Cricket Bat", "Badminton Racket", "Football",
                  "Treadmill", "Bicycle", "Resistance Bands"]),
    CategorySpec("Toys & Games", 199, 4999, 0.30, 0.45, 5,
                 ["Building Blocks", "Board Game", "Remote Control Car", "Jigsaw Puzzle", "Soft Toy",
                  "Doll House"]),
    CategorySpec("Grocery & Gourmet", 99, 1499, 0.10, 0.20, 8,
                 ["Basmati Rice 5kg", "Green Tea", "Dry Fruits Pack", "Coffee Powder", "Masala Combo",
                  "Olive Oil 1L", "Dark Chocolate Box"]),
]

# --- Business behaviour ----------------------------------------------------------------

# Relative order volume by month (Diwali / festive sales in October and November).
MONTH_WEIGHT = {1: 1.0, 2: 0.9, 3: 0.95, 4: 0.9, 5: 0.95, 6: 0.9,
                7: 1.0, 8: 1.1, 9: 1.1, 10: 1.5, 11: 1.4, 12: 1.2}
FESTIVE_MONTHS = {10, 11}

# Products' current price/cost are today's values; older orders were priced lower.
ANNUAL_PRICE_GROWTH = 0.06
ANNUAL_COST_GROWTH = 0.05
MIN_MARKUP = 1.03  # a discounted unit_price never drops below unit_cost * 1.03

ITEMS_PER_ORDER = [1, 2, 3, 4, 5]
ITEMS_PER_ORDER_WEIGHTS = [50, 28, 13, 6, 3]

PREPAID_METHODS = ["upi", "credit_card", "debit_card", "net_banking", "wallet"]
PREPAID_METHOD_WEIGHTS = [45, 18, 16, 11, 10]
COD_SHARE = 0.18  # share of eligible orders paid by cash on delivery
COD_LIMIT = Decimal("50000")  # orders above this total cannot use cash on delivery
FAILED_ATTEMPT_RATE = 0.07  # prepaid orders that had one failed attempt before succeeding

TABLE_COLUMNS = {  # insert order: parents before children
    "categories": ["category_id", "name"],
    "customers": ["customer_id", "full_name", "email", "city", "state", "country", "signup_date"],
    "products": ["product_id", "category_id", "name", "price", "cost", "stock_quantity"],
    "orders": ["order_id", "customer_id", "order_date", "status"],
    "order_items": ["order_item_id", "order_id", "product_id", "quantity", "unit_price", "unit_cost"],
    "payments": ["payment_id", "order_id", "payment_date", "amount", "payment_method", "status"],
}


# --- Helpers ---------------------------------------------------------------------------

def money(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def retail_price(value: float) -> float:
    """Round to a typical shelf price: 349, 1,499, 24,999."""
    step = 10 if value < 1000 else 100
    return max(round(value / step), 1) * step - 1


def random_date(rng: random.Random, first: date, last: date) -> date:
    return first + timedelta(days=rng.randint(0, (last - first).days))


MAX_MONTH_WEIGHT = max(MONTH_WEIGHT.values())


def random_order_date(rng: random.Random, earliest: date) -> date:
    """Pick a date between earliest and DATASET_END_DATE, following monthly seasonality."""
    while True:
        day = random_date(rng, earliest, DATASET_END_DATE)
        if rng.random() * MAX_MONTH_WEIGHT <= MONTH_WEIGHT[day.month]:
            return day


def weighted_choice(rng: random.Random, options: dict[str, int]) -> str:
    return rng.choices(list(options), weights=list(options.values()))[0]


# --- Generators ------------------------------------------------------------------------

def generate_categories() -> list[tuple]:
    return [(category_id, spec.name) for category_id, spec in enumerate(CATEGORIES, start=1)]


def generate_customers(rng: random.Random, fake: Faker) -> list[tuple]:
    city_rows = [(city, state) for city, state, _ in CITIES]
    city_weights = [weight for _, _, weight in CITIES]
    first_signup = START_DATE - timedelta(days=SIGNUP_LEAD_DAYS)
    last_signup = DATASET_END_DATE - timedelta(days=60)
    emails: set[str] = set()

    customers = []
    for customer_id in range(1, NUM_CUSTOMERS + 1):
        first, last = fake.first_name(), fake.last_name()
        local_part = re.sub(r"[^a-z]", "", first.lower()) + "." + re.sub(r"[^a-z]", "", last.lower())
        while True:
            email = f"{local_part}{rng.randint(1, 999)}@{rng.choice(EMAIL_DOMAINS)}"
            if email not in emails:
                emails.add(email)
                break
        city, state = rng.choices(city_rows, weights=city_weights)[0]
        signup_date = random_date(rng, first_signup, last_signup)
        customers.append((customer_id, f"{first} {last}", email, city, state, "India", signup_date))
    return customers


def generate_products(rng: random.Random) -> list[tuple]:
    products = []
    for category_id, spec in enumerate(CATEGORIES, start=1):
        names: list[str] = []
        while len(names) < PRODUCTS_PER_CATEGORY:
            name = f"{rng.choice(BRANDS)} {rng.choice(spec.items)}"
            if name not in names:
                names.append(name)
        for name in names:
            # Log-uniform, so cheaper products are more common than expensive ones.
            raw = spec.min_price * (spec.max_price / spec.min_price) ** rng.random()
            price = min(max(retail_price(raw), spec.min_price), spec.max_price)
            cost = price * (1 - rng.uniform(spec.min_margin, spec.max_margin))
            stock = 0 if rng.random() < 0.04 else rng.randint(5, 60 if price > 20000 else 500)
            products.append((len(products) + 1, category_id, name, money(price), money(cost), stock))
    return products


def generate_orders(rng: random.Random, customers: list[tuple]) -> list[tuple]:
    # Some customers shop far more than others; late sign-ups have less time to order.
    activity = []
    for *_, signup_date in customers:
        active_days = (DATASET_END_DATE - max(signup_date, START_DATE)).days + 1
        activity.append(rng.lognormvariate(0, 1) * active_days / TOTAL_DAYS)

    drafts = []
    for customer in rng.choices(customers, weights=activity, k=NUM_ORDERS):
        customer_id, signup_date = customer[0], customer[-1]
        drafts.append((random_order_date(rng, max(signup_date, START_DATE)), customer_id))
    drafts.sort()  # order_id increases with order_date

    return [(order_id, customer_id, order_date, order_status(rng, order_date))
            for order_id, (order_date, customer_id) in enumerate(drafts, start=1)]


def order_status(rng: random.Random, order_date: date) -> str:
    """Recent orders are still in progress; older orders have reached a final status."""
    age = (DATASET_END_DATE - order_date).days
    if age < 3:
        return weighted_choice(rng, {"pending": 70, "shipped": 20, "cancelled": 10})
    if age < 10:
        return weighted_choice(rng, {"shipped": 55, "delivered": 35, "cancelled": 10})
    return weighted_choice(rng, {"delivered": 84, "cancelled": 9, "returned": 7})


def price_at(rng: random.Random, product: tuple, order_date: date) -> tuple[Decimal, Decimal]:
    """unit_price and unit_cost for a product on a past date (inflation + occasional sale)."""
    _, _, _, current_price, current_cost, _ = product
    years_ago = (DATASET_END_DATE - order_date).days / 365

    unit_cost = float(current_cost) / (1 + ANNUAL_COST_GROWTH) ** years_ago
    list_price = float(current_price) / (1 + ANNUAL_PRICE_GROWTH) ** years_ago

    festive = order_date.month in FESTIVE_MONTHS
    discount = 0.0
    if rng.random() < (0.5 if festive else 0.15):
        discount = rng.uniform(0.10, 0.30) if festive else rng.uniform(0.05, 0.15)
    unit_price = max(retail_price(list_price * (1 - discount)), unit_cost * MIN_MARKUP)
    return money(unit_price), money(unit_cost)


def quantity_for(rng: random.Random, unit_price: Decimal) -> int:
    if unit_price < 500:
        return rng.choices([1, 2, 3, 4], weights=[55, 28, 12, 5])[0]
    if unit_price < 3000:
        return rng.choices([1, 2, 3], weights=[75, 20, 5])[0]
    return rng.choices([1, 2], weights=[93, 7])[0]


def generate_order_items(rng: random.Random, orders: list[tuple],
                         products: list[tuple]) -> tuple[list[tuple], dict[int, Decimal]]:
    category_popularity = {category_id: spec.popularity for category_id, spec in enumerate(CATEGORIES, start=1)}
    product_weights = [category_popularity[p[1]] * rng.lognormvariate(0, 0.8) for p in products]

    items = []
    order_totals: dict[int, Decimal] = {}
    for order_id, _, order_date, _ in orders:
        count = rng.choices(ITEMS_PER_ORDER, weights=ITEMS_PER_ORDER_WEIGHTS)[0]
        picked: list[tuple] = []
        while len(picked) < count:  # each product at most once per order
            product = rng.choices(products, weights=product_weights)[0]
            if product not in picked:
                picked.append(product)

        total = Decimal("0")
        for product in picked:
            unit_price, unit_cost = price_at(rng, product, order_date)
            quantity = quantity_for(rng, unit_price)
            items.append((len(items) + 1, order_id, product[0], quantity, unit_price, unit_cost))
            total += quantity * unit_price
        order_totals[order_id] = total
    return items, order_totals


def generate_payments(rng: random.Random, orders: list[tuple],
                      order_totals: dict[int, Decimal]) -> list[tuple]:
    payments = []

    def add(order_id: int, payment_date: date, amount: Decimal, method: str, status: str) -> None:
        payments.append((len(payments) + 1, order_id, payment_date, amount, method, status))

    def prepaid_method() -> str:
        return rng.choices(PREPAID_METHODS, weights=PREPAID_METHOD_WEIGHTS)[0]

    for order_id, _, order_date, status in orders:
        total = order_totals[order_id]

        if total <= COD_LIMIT and rng.random() < COD_SHARE:
            # Cash on delivery: money is collected only when the order is delivered.
            delivered_on = min(order_date + timedelta(days=rng.randint(2, 6)), DATASET_END_DATE)
            if status == "delivered":
                add(order_id, delivered_on, total, "cash_on_delivery", "completed")
            elif status == "returned":
                add(order_id, delivered_on, total, "cash_on_delivery", "refunded")
            elif status in ("pending", "shipped"):
                add(order_id, order_date, total, "cash_on_delivery", "pending")
            # A cancelled COD order was never paid, so it has no payment record.
            continue

        # Prepaid: the customer pays when placing the order.
        if status == "cancelled" and rng.random() < 0.4:
            # Payment failed, so the order was cancelled.
            for _ in range(rng.choice([1, 1, 2])):
                add(order_id, order_date, total, prepaid_method(), "failed")
            continue

        if rng.random() < FAILED_ATTEMPT_RATE:
            add(order_id, order_date, total, prepaid_method(), "failed")

        if status in ("delivered", "shipped"):
            final_status = "completed"
        elif status == "pending":
            final_status = "completed" if rng.random() < 0.75 else "pending"
        else:  # cancelled after paying, or returned
            final_status = "refunded"
        add(order_id, order_date, total, prepaid_method(), final_status)
    return payments


def generate() -> dict[str, list[tuple]]:
    rng = random.Random(SEED)
    fake = Faker("en_IN")
    fake.seed_instance(SEED)

    categories = generate_categories()
    customers = generate_customers(rng, fake)
    products = generate_products(rng)
    orders = generate_orders(rng, customers)
    order_items, order_totals = generate_order_items(rng, orders, products)
    payments = generate_payments(rng, orders, order_totals)
    return {"categories": categories, "customers": customers, "products": products,
            "orders": orders, "order_items": order_items, "payments": payments}


# --- Output and loading ----------------------------------------------------------------

def print_summary(data: dict[str, list[tuple]]) -> None:
    for table, rows in data.items():
        print(f"{table:<12} {len(rows):>6,} rows")

    orders, items, payments = data["orders"], data["order_items"], data["payments"]
    print(f"order dates  {orders[0][2]} to {orders[-1][2]}")
    print("order status  ", dict(Counter(o[3] for o in orders).most_common()))
    print("payment status", dict(Counter(p[5] for p in payments).most_common()))
    print("payment method", dict(Counter(p[4] for p in payments).most_common()))

    completed = {o[0] for o in orders if o[3] == "delivered"}
    revenue = sum(i[3] * i[4] for i in items if i[1] in completed)
    profit = sum(i[3] * (i[4] - i[5]) for i in items if i[1] in completed)
    successful = sum(p[3] for p in payments if p[5] == "completed")
    print(f"Revenue  INR {revenue:,.2f}  (delivered orders)")
    print(f"Profit   INR {profit:,.2f}  (margin {profit / revenue:.1%})")
    print(f"AOV      INR {revenue / len(completed):,.2f}")
    print(f"Successful payments INR {successful:,.2f}")


def load(conn: psycopg.Connection, data: dict[str, list[tuple]]) -> None:
    """Replace all rows in one transaction: either everything is loaded or nothing changes."""
    with conn.transaction(), conn.cursor() as cur:
        # Listed child -> parent; one TRUNCATE of all six tables is safe for the foreign keys.
        cur.execute("TRUNCATE payments, order_items, orders, products, categories, customers RESTART IDENTITY")
        for table, columns in TABLE_COLUMNS.items():
            with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
                for row in data[table]:
                    copy.write_row(row)
            # IDs were inserted explicitly, so move each identity sequence past the highest ID.
            id_column = columns[0]
            cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', '{id_column}'), "
                        f"(SELECT max({id_column}) FROM {table}))")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and load DataPilot's synthetic dataset.")
    parser.add_argument("--dry-run", action="store_true",
                        help="generate the data and print a summary without touching the database")
    args = parser.parse_args()

    data = generate()
    print_summary(data)
    if args.dry_run:
        print("Dry run: the database was not touched.")
        return

    settings = DatabaseSettings()
    try:
        with connect(settings.database_url) as conn:
            load(conn, data)
    except psycopg.Error as error:
        # Never show the raw message: connection errors can include host and user names.
        constraint = getattr(getattr(error, "diag", None), "constraint_name", None)
        detail = f", constraint {constraint}" if constraint else ""
        raise SystemExit(f"Database error ({type(error).__name__}{detail}). "
                         "The transaction was rolled back; no data was changed.") from None
    print("Seed data loaded.")


if __name__ == "__main__":
    main()
