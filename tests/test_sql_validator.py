"""Tests for the SQL safety validator (backend/app/sql_validator.py)."""

import pytest

from app.sql_validator import MAX_LIMIT, UnsafeSQLError, validate_sql


def limit_of(sql: str) -> str:
    """The LIMIT clause at the end of validated SQL, e.g. 'LIMIT 501'."""
    return sql[sql.rindex("LIMIT"):]


# When the validator adds or caps a LIMIT it asks for one row more than MAX_LIMIT, so the executor
# can tell whether rows were left out (see test_truncation.py).
ADDED_LIMIT = f"LIMIT {MAX_LIMIT + 1}"


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
    assert limit_of(validate_sql("SELECT * FROM orders")) == ADDED_LIMIT


def test_valid_limit_is_kept():
    assert limit_of(validate_sql("SELECT * FROM orders ORDER BY order_date DESC LIMIT 10")) == "LIMIT 10"


def test_limit_equal_to_maximum_is_kept():
    assert limit_of(validate_sql(f"SELECT * FROM orders LIMIT {MAX_LIMIT}")) == f"LIMIT {MAX_LIMIT}"


def test_limit_one_past_the_maximum_is_kept_and_anything_larger_is_capped():
    assert limit_of(validate_sql(f"SELECT * FROM orders LIMIT {MAX_LIMIT + 1}")) == ADDED_LIMIT
    assert limit_of(validate_sql(f"SELECT * FROM orders LIMIT {MAX_LIMIT + 2}")) == ADDED_LIMIT


def test_oversized_limit_is_capped():
    assert limit_of(validate_sql("SELECT * FROM orders LIMIT 1000000")) == ADDED_LIMIT


def test_limit_all_is_capped():
    assert limit_of(validate_sql("SELECT * FROM orders LIMIT ALL")) == ADDED_LIMIT


def test_custom_maximum_is_respected():
    assert limit_of(validate_sql("SELECT * FROM orders LIMIT 80", max_limit=50)) == "LIMIT 51"
    assert limit_of(validate_sql("SELECT * FROM orders", max_limit=50)) == "LIMIT 51"
    assert limit_of(validate_sql("SELECT * FROM orders LIMIT 50", max_limit=50)) == "LIMIT 50"


def test_offset_is_kept_when_limit_is_added():
    result = validate_sql("SELECT * FROM orders ORDER BY order_id OFFSET 20")
    assert "OFFSET 20" in result and ADDED_LIMIT in result


def test_limit_applies_to_whole_union():
    result = validate_sql("SELECT city FROM customers UNION SELECT name FROM categories")
    assert result.endswith(ADDED_LIMIT)


def test_inner_limit_is_left_alone_and_outer_limit_added():
    result = validate_sql("SELECT * FROM (SELECT * FROM orders LIMIT 5000) AS recent")
    assert "LIMIT 5000" in result and result.endswith(ADDED_LIMIT)


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


# --- SQLGlot-specific edge cases ---------------------------------------------------------
# These pin down how SQLGlot represents tricky PostgreSQL syntax, so a SQLGlot upgrade that
# changes the parse tree shows up as a failing test instead of a silent security gap.

import sqlglot  # noqa: E402
from sqlglot import exp  # noqa: E402


@pytest.mark.parametrize("sql", [
    "SELECT * INTO stolen FROM customers",
    "SELECT * INTO TEMP stolen FROM customers",
    "SELECT * INTO UNLOGGED TABLE stolen FROM customers",
    "SELECT customer_id INTO stolen FROM customers WHERE city = 'Pune' ORDER BY 1",
])
def test_every_form_of_select_into_is_blocked(sql):
    assert_blocked(sql, "INTO is not allowed")


@pytest.mark.parametrize("sql", [
    "SELECT * FROM orders FOR UPDATE",
    "SELECT * FROM orders FOR NO KEY UPDATE",
    "SELECT * FROM orders FOR SHARE",
    "SELECT * FROM orders FOR KEY SHARE",
    "SELECT * FROM orders FOR UPDATE SKIP LOCKED",
    "SELECT * FROM (SELECT * FROM orders FOR UPDATE) AS locked",
])
def test_every_row_locking_clause_is_blocked(sql):
    assert_blocked(sql, "LOCK is not allowed")


def test_limit_all_is_replaced_with_custom_maximum():
    assert limit_of(validate_sql("SELECT * FROM orders LIMIT ALL", max_limit=25)) == "LIMIT 26"


def test_limit_all_on_a_union_is_replaced():
    result = validate_sql("SELECT city FROM customers UNION SELECT name FROM categories LIMIT ALL")
    assert result.endswith(ADDED_LIMIT) and "ALL" not in result


@pytest.mark.parametrize("sql", [
    "SELECT * FROM generate_series(1, 10)",
    "SELECT * FROM unnest(ARRAY[1, 2, 3])",
    "SELECT * FROM pg_ls_dir('.')",
    "SELECT * FROM json_to_recordset('[{\"a\": 1}]') AS t(a INT)",
    "SELECT * FROM customers CROSS JOIN LATERAL generate_series(1, 3) AS g",
    "SELECT * FROM dblink('host=evil.example', 'SELECT 1') AS t(x INT)",
])
def test_no_function_is_allowed_as_a_table_source(sql):
    # No table-producing function is on a safe list in v1, so all of them are rejected.
    with pytest.raises(UnsafeSQLError):
        validate_sql(sql)


@pytest.mark.parametrize("name", ["pg_sleep", "pg_read_file", "lo_import", "dblink"])
def test_dangerous_functions_parse_as_anonymous_and_are_blocked(name):
    parsed = sqlglot.parse_one(f"SELECT {name}('x')", read="postgres")
    assert isinstance(parsed.find(exp.Func), exp.Anonymous)  # SQLGlot has no class for them
    assert_blocked(f"SELECT {name}('x')", f"Function '{name}' is not allowed")


@pytest.mark.parametrize("sql, name", [
    ("SELECT AGE(DATE '2026-09-30', signup_date) FROM customers", "age"),
    ("SELECT MAKE_DATE(2026, 9, 30)", "make_date"),
    ("SELECT EVERY(stock_quantity > 0) FROM products", "every"),
])
def test_safe_postgres_functions_parsed_as_anonymous_are_allowed(sql, name):
    parsed = sqlglot.parse_one(sql, read="postgres")
    anonymous = [f.name.lower() for f in parsed.find_all(exp.Anonymous)]
    assert name in anonymous  # SQLGlot has no class for them either
    assert validate_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT clock_timestamp()",
    "SELECT inet_client_addr()",
    "SELECT json_build_object('id', customer_id) FROM customers",
])
def test_anonymous_functions_not_on_the_safe_list_are_blocked(sql):
    assert_blocked(sql, "not recognised as safe")


# --- Query shapes ----------------------------------------------------------------------

def test_nested_subqueries_are_allowed():
    sql = """
        SELECT full_name FROM customers
        WHERE customer_id IN (
            SELECT customer_id FROM orders
            WHERE order_id IN (
                SELECT order_id FROM order_items
                WHERE product_id IN (SELECT product_id FROM products WHERE price > 10000)))
    """
    assert validate_sql(sql).endswith(ADDED_LIMIT)


def test_forbidden_table_deep_inside_nested_subqueries_is_blocked():
    sql = """
        SELECT * FROM customers WHERE customer_id IN (
            SELECT customer_id FROM orders WHERE order_id IN (
                SELECT order_id FROM order_items WHERE product_id IN (SELECT usesysid FROM pg_user)))
    """
    assert_blocked(sql, "Table 'pg_user' is not allowed")


@pytest.mark.parametrize("operator", ["UNION", "UNION ALL", "INTERSECT", "EXCEPT"])
def test_set_operations_are_allowed(operator):
    result = validate_sql(f"SELECT city FROM customers {operator} SELECT name FROM categories")
    assert operator in result and result.endswith(ADDED_LIMIT)


@pytest.mark.parametrize("sql, reason", [
    ("SELECT city FROM customers UNION ALL SELECT usename FROM pg_user", "Table 'pg_user'"),
    ("SELECT city FROM customers UNION SELECT table_name FROM information_schema.tables", "Schema 'information_schema'"),
    ("SELECT 1 FROM customers UNION ALL SELECT pg_sleep(5)", "Function 'pg_sleep'"),
])
def test_unsafe_second_branch_of_a_union_is_blocked(sql, reason):
    assert_blocked(sql, reason)


@pytest.mark.parametrize("sql", [
    "-- monthly revenue\nSELECT * FROM orders",
    "/* generated by the model */ SELECT * FROM orders",
    "/* a */ -- b\n/* c */ SELECT * FROM orders",
])
def test_comments_before_sql_are_allowed_and_removed(sql):
    result = validate_sql(sql)
    assert result == f"SELECT * FROM orders {ADDED_LIMIT}"


def test_semicolon_and_trailing_comment_after_a_valid_query():
    assert validate_sql("SELECT * FROM orders; -- done") == f"SELECT * FROM orders {ADDED_LIMIT}"


@pytest.mark.parametrize("sql", [
    "SELECT * FROM orders;DELETE FROM orders",
    "SELECT * FROM orders; /* harmless? */ DELETE FROM orders;",
    "SELECT 1 FROM customers;\nSELECT 2 FROM customers",
])
def test_statements_separated_by_semicolons_are_blocked(sql):
    assert_blocked(sql, "exactly one SQL statement")


# --- Quoted identifiers and schema qualification ---------------------------------------
# Decision: public.<allowed table> is allowed (it is the same table); every other schema is
# rejected. Identifiers follow PostgreSQL rules: unquoted names are case-insensitive, quoted
# names are case-sensitive, so "Customers" is a different (non-allowed) table.

@pytest.mark.parametrize("sql", [
    'SELECT "full_name", "city" FROM "customers"',
    'SELECT * FROM "public"."customers"',
    "SELECT * FROM public.customers",
    "SELECT * FROM PUBLIC.Customers",
    'SELECT o."order_id" FROM "orders" AS o JOIN public.order_items oi ON oi.order_id = o.order_id',
    'WITH "Recent" AS (SELECT * FROM orders WHERE order_date > DATE \'2026-09-01\') SELECT * FROM "Recent"',
])
def test_quoted_and_public_qualified_names_are_allowed(sql):
    assert validate_sql(sql)


@pytest.mark.parametrize("sql, reason", [
    ('SELECT * FROM "Customers"', "Table 'Customers' is not allowed"),
    ('SELECT * FROM "ORDERS"', "Table 'ORDERS' is not allowed"),
    ('SELECT * FROM "PUBLIC".customers', "Schema 'PUBLIC' is not allowed"),
    ("SELECT * FROM public.secrets", "Table 'secrets' is not allowed"),
    ("SELECT * FROM public.pg_user", "Table 'pg_user' is not allowed"),
    # Quoted CTE "Recent" does not match unquoted recent, which PostgreSQL reads as a real table.
    ('WITH "Recent" AS (SELECT 1) SELECT * FROM recent', "Table 'recent' is not allowed"),
])
def test_case_sensitive_quoted_names_follow_postgres_rules(sql, reason):
    assert_blocked(sql, reason)


# --- What may appear in FROM / JOIN ----------------------------------------------------

def test_lateral_subquery_is_allowed():
    sql = """
        SELECT c.full_name, last_order.order_date
        FROM customers c
        CROSS JOIN LATERAL (
            SELECT o.order_date FROM orders o WHERE o.customer_id = c.customer_id
            ORDER BY o.order_date DESC LIMIT 1) AS last_order
    """
    assert validate_sql(sql)


@pytest.mark.parametrize("sql, reason", [
    ("SELECT * FROM unnest(ARRAY[1, 2, 3])", "got UNNEST"),
    ("SELECT * FROM customers, unnest(ARRAY[1]) AS u", "got UNNEST"),
    ("SELECT * FROM customers CROSS JOIN LATERAL generate_series(1, 3) AS g", "got EXPLODINGGENERATESERIES"),
    ("SELECT * FROM (VALUES (1), (2)) AS v(x)", "got VALUES"),
    ("SELECT * FROM ROWS FROM (generate_series(1, 3))", "Functions are not allowed as a table source"),
])
def test_non_table_row_sources_are_blocked(sql, reason):
    assert_blocked(sql, reason)
