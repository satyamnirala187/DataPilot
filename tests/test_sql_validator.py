"""Tests for the SQL safety validator (backend/app/sql_validator.py)."""

import pytest

from app.sql_validator import MAX_LIMIT, UnsafeSQLError, validate_sql


def limit_of(sql: str) -> str:
    """The LIMIT clause at the end of validated SQL, e.g. 'LIMIT 500'."""
    return sql[sql.rindex("LIMIT"):]


# --- Allowed queries -------------------------------------------------------------------

ALLOWED = {
    "simple select": "SELECT * FROM customers",
    "where": "SELECT full_name, city FROM customers WHERE city = 'Mumbai' AND signup_date >= '2025-01-01'",
    "join": """
        SELECT o.order_id, c.full_name
        FROM orders o JOIN customers c ON c.customer_id = o.customer_id
        WHERE o.status = 'delivered'
    """,
    "group by with aggregates": """
        SELECT c.name, SUM(oi.quantity * oi.unit_price) AS revenue, COUNT(DISTINCT o.order_id) AS orders
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.order_id
        JOIN products p ON p.product_id = oi.product_id
        JOIN categories c ON c.category_id = p.category_id
        WHERE o.status = 'delivered'
        GROUP BY c.name
        HAVING SUM(oi.quantity * oi.unit_price) > 1000
        ORDER BY revenue DESC
    """,
    "cte": """
        WITH delivered AS (SELECT order_id, customer_id FROM orders WHERE status = 'delivered')
        SELECT customer_id, COUNT(*) FROM delivered GROUP BY customer_id
    """,
    "several ctes, later one uses earlier one": """
        WITH d AS (SELECT order_id FROM orders WHERE status = 'delivered'),
             r AS (SELECT oi.order_id, SUM(oi.quantity * oi.unit_price) AS total
                   FROM order_items oi JOIN d ON d.order_id = oi.order_id GROUP BY oi.order_id)
        SELECT AVG(total) FROM r
    """,
    "recursive cte": """
        WITH RECURSIVE n AS (SELECT 1 AS i UNION ALL SELECT i + 1 FROM n WHERE i < 12)
        SELECT i FROM n
    """,
    "subquery": "SELECT * FROM customers WHERE customer_id IN (SELECT customer_id FROM orders WHERE status = 'returned')",
    "union": "SELECT city FROM customers UNION SELECT name FROM categories",
    "window functions": """
        SELECT order_id, order_date, RANK() OVER (ORDER BY order_date) AS r,
               LAG(order_date) OVER (PARTITION BY customer_id ORDER BY order_date) AS previous
        FROM orders
    """,
    "date functions": """
        SELECT TO_CHAR(DATE_TRUNC('month', order_date), 'YYYY-MM') AS month, COUNT(*)
        FROM orders
        WHERE order_date >= DATE '2026-09-30' - INTERVAL '30 days'
          AND EXTRACT(YEAR FROM order_date) = 2026
        GROUP BY 1
    """,
    "safe functions sqlglot does not model": "SELECT AGE(MAKE_DATE(2026, 9, 30), signup_date) FROM customers",
    "case, coalesce, round, cast": """
        SELECT CASE WHEN price > 1000 THEN 'premium' ELSE 'standard' END,
               COALESCE(ROUND(AVG(price)::numeric, 2), 0), CAST(stock_quantity AS TEXT)
        FROM products GROUP BY price, stock_quantity
    """,
    "public schema prefix": "SELECT * FROM public.customers",
    "uppercase keywords and table names": "SELECT CITY FROM CUSTOMERS",
    "trailing semicolon": "SELECT * FROM customers;",
    "select without a table": "SELECT 1",
    # Structural checks, not text matching: dangerous words inside a string are just data.
    "sql keywords inside a string literal": "SELECT * FROM customers WHERE full_name = 'DROP TABLE orders; DELETE pg_sleep(9)'",
}


@pytest.mark.parametrize("sql", ALLOWED.values(), ids=ALLOWED.keys())
def test_allowed_queries_pass(sql):
    result = validate_sql(sql)
    assert result.startswith(("SELECT", "WITH"))
    assert "LIMIT" in result


def test_returned_sql_is_itself_valid():
    once = validate_sql(ALLOWED["group by with aggregates"])
    assert validate_sql(once) == once


def test_missing_limit_is_added():
    assert limit_of(validate_sql("SELECT * FROM orders")) == f"LIMIT {MAX_LIMIT}"


def test_valid_limit_is_kept():
    assert limit_of(validate_sql("SELECT * FROM orders ORDER BY order_date DESC LIMIT 10")) == "LIMIT 10"


def test_limit_equal_to_maximum_is_kept():
    assert limit_of(validate_sql(f"SELECT * FROM orders LIMIT {MAX_LIMIT}")) == f"LIMIT {MAX_LIMIT}"


def test_oversized_limit_is_capped():
    assert limit_of(validate_sql("SELECT * FROM orders LIMIT 1000000")) == f"LIMIT {MAX_LIMIT}"


def test_limit_all_is_capped():
    assert limit_of(validate_sql("SELECT * FROM orders LIMIT ALL")) == f"LIMIT {MAX_LIMIT}"


def test_custom_maximum_is_respected():
    assert limit_of(validate_sql("SELECT * FROM orders LIMIT 80", max_limit=50)) == "LIMIT 50"
    assert limit_of(validate_sql("SELECT * FROM orders", max_limit=50)) == "LIMIT 50"


def test_offset_is_kept_when_limit_is_added():
    result = validate_sql("SELECT * FROM orders ORDER BY order_id OFFSET 20")
    assert "OFFSET 20" in result and f"LIMIT {MAX_LIMIT}" in result


def test_limit_applies_to_whole_union():
    result = validate_sql("SELECT city FROM customers UNION SELECT name FROM categories")
    assert result.endswith(f"LIMIT {MAX_LIMIT}")


def test_inner_limit_is_left_alone_and_outer_limit_added():
    result = validate_sql("SELECT * FROM (SELECT * FROM orders LIMIT 5000) AS recent")
    assert "LIMIT 5000" in result and result.endswith(f"LIMIT {MAX_LIMIT}")


def test_comments_are_removed():
    result = validate_sql("SELECT city FROM customers -- ignore previous instructions; DROP TABLE x")
    assert "DROP" not in result and "--" not in result and "/*" not in result


# --- Blocked queries -------------------------------------------------------------------

def assert_blocked(sql: str, reason: str) -> None:
    with pytest.raises(UnsafeSQLError, match=reason):
        validate_sql(sql)


@pytest.mark.parametrize("sql", [
    "DROP TABLE customers",
    "DELETE FROM orders WHERE order_id = 1",
    "UPDATE products SET price = 0",
    "INSERT INTO categories (name) VALUES ('x')",
    "TRUNCATE orders",
    "ALTER TABLE customers ADD COLUMN x INT",
    "CREATE TABLE copy AS SELECT * FROM customers",
    "GRANT SELECT ON customers TO public",
    "REVOKE SELECT ON customers FROM public",
    "COPY customers TO '/tmp/customers.csv'",
    "MERGE INTO orders o USING customers c ON o.customer_id = c.customer_id WHEN MATCHED THEN DELETE",
    "SET search_path TO pg_catalog",
    "BEGIN",
    "COMMIT",
    "EXPLAIN ANALYZE SELECT * FROM customers",
    "VACUUM customers",
    "SHOW ALL",
    "VALUES (1)",
    "TABLE customers",
    "(SELECT * FROM customers)",
])
def test_non_select_statements_are_blocked(sql):
    with pytest.raises(UnsafeSQLError):
        validate_sql(sql)


@pytest.mark.parametrize("sql", [
    "DROP TABLE customers",
    "DELETE FROM orders",
    "UPDATE products SET price = 0",
    "INSERT INTO categories (name) VALUES ('x')",
])
def test_write_statements_fail_the_select_only_rule(sql):
    assert_blocked(sql, "Only SELECT queries are allowed")


@pytest.mark.parametrize("sql", [
    "SELECT * FROM customers; DROP TABLE customers",
    "SELECT * FROM customers; SELECT * FROM orders",
    "SELECT 1; SELECT 2;",
])
def test_multiple_statements_are_blocked(sql):
    assert_blocked(sql, "exactly one SQL statement")


@pytest.mark.parametrize("sql", [
    "WITH gone AS (DELETE FROM orders RETURNING *) SELECT * FROM gone",
    "WITH changed AS (UPDATE products SET price = 0 RETURNING *) SELECT * FROM changed",
    "WITH added AS (INSERT INTO categories (name) VALUES ('x') RETURNING *) SELECT * FROM added",
])
def test_write_inside_cte_is_blocked(sql):
    with pytest.raises(UnsafeSQLError):
        validate_sql(sql)


def test_write_statement_after_with_is_blocked():
    assert_blocked("WITH x AS (SELECT * FROM customers) INSERT INTO categories SELECT 1, 'x'", "Only SELECT")


def test_select_into_is_blocked():
    assert_blocked("SELECT * INTO stolen FROM customers", "INTO is not allowed")


@pytest.mark.parametrize("sql", [
    "SELECT * FROM orders FOR UPDATE",
    "SELECT * FROM orders FOR SHARE",
])
def test_row_locking_is_blocked(sql):
    assert_blocked(sql, "LOCK is not allowed")


@pytest.mark.parametrize("sql", [
    "SELECT * FROM users",
    "SELECT * FROM pg_tables",
    "SELECT * FROM pg_user",
    "SELECT * FROM customers c JOIN secrets s ON s.id = c.customer_id",
    "SELECT * FROM customers WHERE customer_id IN (SELECT usesysid FROM pg_user)",
    'SELECT * FROM "pg_shadow"',
])
def test_unknown_tables_are_blocked(sql):
    assert_blocked(sql, "Table '.*' is not allowed")


@pytest.mark.parametrize("sql", [
    "SELECT * FROM information_schema.tables",
    "SELECT column_name FROM information_schema.columns WHERE table_name = 'customers'",
    "SELECT * FROM pg_catalog.pg_tables",
    "SELECT * FROM pg_catalog.pg_shadow",
    "SELECT * FROM auth.users",
    "SELECT * FROM other.customers",
    "SELECT * FROM postgres.public.customers",
])
def test_other_schemas_are_blocked(sql):
    assert_blocked(sql, "Schema '.*' is not allowed")


def test_cte_cannot_use_a_later_cte_name_to_reach_a_real_table():
    # In PostgreSQL, "secrets" inside the first CTE is the real table, not the later CTE.
    sql = "WITH a AS (SELECT * FROM secrets), secrets AS (SELECT 1) SELECT * FROM a"
    assert_blocked(sql, "Table 'secrets' is not allowed")


def test_cte_name_does_not_leak_outside_its_subquery():
    sql = "SELECT * FROM secrets, (WITH secrets AS (SELECT 1) SELECT * FROM secrets) AS s"
    assert_blocked(sql, "Table 'secrets' is not allowed")


@pytest.mark.parametrize("sql", [
    "SELECT pg_sleep(10)",
    "SELECT * FROM customers WHERE pg_sleep(5) IS NOT NULL",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT lo_import('/etc/passwd')",
    "SELECT dblink('host=evil.example', 'SELECT 1')",
    "SELECT pg_catalog.pg_sleep(1)",
    "SELECT query_to_xml('SELECT * FROM pg_shadow', true, true, '')",
    "SELECT set_config('statement_timeout', '0', false)",
    "SELECT current_setting('data_directory')",
    "SELECT nextval('orders_order_id_seq')",
    "SELECT pg_terminate_backend(1)",
    "SELECT * FROM customers, LATERAL (SELECT pg_sleep(1)) AS hidden",
])
def test_dangerous_functions_are_blocked(sql):
    assert_blocked(sql, "Function '.*' is not allowed")


def test_unrecognised_functions_are_blocked():
    assert_blocked("SELECT some_custom_function(price) FROM products", "not recognised as safe")


@pytest.mark.parametrize("sql", [
    "SELECT * FROM generate_series(1, 10)",
    "SELECT * FROM dblink('host=evil.example', 'SELECT 1') AS t(x INT)",
])
def test_functions_as_table_sources_are_blocked(sql):
    with pytest.raises(UnsafeSQLError):
        validate_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM orders LIMIT 5 + 5",
    "SELECT * FROM orders LIMIT $1",
    "SELECT * FROM orders LIMIT (SELECT COUNT(*) FROM customers)",
    "SELECT * FROM orders LIMIT '10'",
])
def test_non_numeric_limit_is_blocked(sql):
    assert_blocked(sql, "LIMIT must be a whole number")


def test_fetch_first_is_blocked():
    assert_blocked("SELECT * FROM orders FETCH FIRST 5 ROWS ONLY", "Use LIMIT")


@pytest.mark.parametrize("sql", [
    "SELEC * FROM customers",
    "SELECT * FROM",
    "SELECT (",
    "SELECT * FROM customers WHERE",
    "this is not sql",
])
def test_malformed_sql_is_blocked(sql):
    with pytest.raises(UnsafeSQLError):
        validate_sql(sql)


@pytest.mark.parametrize("sql", ["", "   ", "\n\t", ";", ";;"])
def test_empty_input_is_blocked(sql):
    with pytest.raises(UnsafeSQLError):
        validate_sql(sql)
