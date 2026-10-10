"""Tests for the SQL safety validator (backend/app/sql_validator.py)."""

import pytest

from app.sql_validator import ALLOWED_SCHEMAS, ALLOWED_TABLES, MAX_LIMIT, UnsafeSQLError, validate_sql


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
    "left join using": "SELECT o.order_id, p.amount FROM orders o LEFT JOIN payments p USING (order_id)",
    "join on a compound condition": """
        SELECT o.order_id FROM orders o
        JOIN payments p ON p.order_id = o.order_id AND p.status = 'completed'
    """,
    "min, max and date grouping": """
        SELECT DATE_TRUNC('month', order_date) AS month, MIN(order_date), MAX(order_date), COUNT(*)
        FROM orders GROUP BY 1 ORDER BY 1
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


# --- Resource amplification ------------------------------------------------------------

@pytest.mark.parametrize("sql, name", [
    ("SELECT repeat('x', 300000000)", "repeat"),
    ("SELECT REPEAT('x', 300000000) AS big", "repeat"),
    ("SELECT lpad('x', 300000000, 'x')", "lpad"),
    ("SELECT Rpad('x', 300000000, 'x')", "rpad"),
    ("SELECT generate_series(1, 100000000) AS n", "generate_series"),
    ("SELECT string_agg(email, ',') FROM customers", "string_agg"),
    ("SELECT array_agg(email) FROM customers", "array_agg"),
    ("SELECT json_agg(email) FROM customers", "json_agg"),
    ("SELECT json_object_agg(email, city) FROM customers", "json_object_agg"),
])
def test_amplifying_functions_are_blocked(sql, name):
    assert_blocked(sql, f"Function '{name}' is not allowed")


@pytest.mark.parametrize("sql", [
    # hidden inside otherwise normal business SQL, a CTE, a subquery or a join condition
    "SELECT c.name, repeat(c.name, 1000000) FROM categories c",
    "WITH d AS (SELECT lpad(full_name, 100000000, '*') AS x FROM customers) SELECT x FROM d",
    "SELECT * FROM customers WHERE city IN (SELECT rpad(city, 100000000) FROM customers)",
    """SELECT o.order_id FROM orders o
       JOIN order_items oi ON oi.order_id = o.order_id AND repeat('a', 9) = 'a'""",
    "SELECT COUNT(*) FROM (SELECT generate_series(1, 1000000000) AS n) AS s",
    "SELECT SUM(oi.quantity) AS units, string_agg(p.name, ',') AS names FROM order_items oi JOIN products p ON p.product_id = oi.product_id",
])
def test_amplifying_functions_are_blocked_anywhere(sql):
    assert_blocked(sql, "is not allowed")


@pytest.mark.parametrize("sql", [
    "SELECT public.repeat('x', 3)",
    "SELECT pg_catalog.lpad('x', 3)",
    "SELECT jsonb_agg(email) FROM customers",
    "SELECT array_fill(0, ARRAY[100000000])",
    "SELECT string_to_table('a,b', ',')",
])
def test_other_spellings_and_relatives_are_rejected_as_unrecognised(sql):
    assert_blocked(sql, "not recognised as safe")


@pytest.mark.parametrize("sql", [
    "SELECT 1 FROM orders o CROSS JOIN order_items oi",
    "SELECT 1 FROM orders o, order_items oi",
    "SELECT 1 FROM orders o, order_items oi WHERE oi.order_id = o.order_id",  # comma joins, even filtered
    "SELECT 1 FROM orders o JOIN order_items oi ON TRUE",
    "SELECT 1 FROM orders o JOIN order_items oi ON 1 = 1",
    "SELECT 1 FROM orders NATURAL JOIN payments",
    "SELECT 1 FROM orders o JOIN order_items oi",
    "WITH a AS (SELECT * FROM orders CROSS JOIN customers) SELECT COUNT(*) FROM a",
])
def test_cartesian_joins_are_blocked(sql):
    assert_blocked(sql, "no cross joins")


JOIN_ON = "SELECT o.order_id FROM orders o JOIN order_items oi ON "


@pytest.mark.parametrize("condition", [
    "o.order_id = o.order_id",  # tests only the left side: every order_items row matches
    "oi.order_id = oi.order_id",  # tests only the joined side
    "o.order_id IS NOT NULL",
    "oi.order_id IS NOT NULL",
    "o.order_id > 0",
    "oi.quantity > 0",
    "OI.ORDER_ID = OI.ORDER_ID",  # unquoted names are case-insensitive
    "Oi.order_id = oI.order_id",
    "oi.order_id = o.order_id OR TRUE",  # the link is ORed away
    "(oi.order_id = o.order_id OR oi.quantity > 0)",
    "TRUE AND oi.quantity > 0",
    "oi.order_id > o.order_id",  # links both sides, but not by equality
    "order_id = oi.order_id",  # unqualified: cannot be attributed, so fails closed
    "oi.order_id = (SELECT MAX(x.order_id) FROM orders x)",
])
def test_join_conditions_that_do_not_link_both_sides_are_blocked(condition):
    assert_blocked(JOIN_ON + condition, "no cross joins")


@pytest.mark.parametrize("sql", [
    "SELECT 1 FROM orders o JOIN order_items o ON o.order_id = o.order_id",  # one alias for both sides
    """SELECT 1 FROM orders o JOIN customers c ON c.customer_id = o.customer_id
       JOIN payments p ON c.customer_id = o.customer_id""",  # the third table is never linked
    "SELECT * FROM customers WHERE customer_id IN (SELECT o.customer_id FROM orders o JOIN payments p ON p.amount > 0)",
])
def test_unlinked_joins_are_blocked_in_any_position(sql):
    assert_blocked(sql, "no cross joins")


@pytest.mark.parametrize("sql", [
    JOIN_ON + "oi.order_id = o.order_id",
    JOIN_ON + "o.order_id = oi.order_id",
    JOIN_ON + "oi.order_id = o.order_id AND oi.quantity > 0",
    JOIN_ON + "(oi.quantity > 0 AND (oi.order_id = o.order_id))",
    JOIN_ON + "OI.Order_Id = O.order_id",
    "SELECT o.order_id FROM orders o LEFT JOIN payments p ON p.order_id = o.order_id",
    "SELECT o.order_id FROM orders o JOIN payments p USING (order_id)",
    """SELECT c.name, SUM(oi.quantity) FROM order_items oi
       JOIN products p ON p.product_id = oi.product_id
       JOIN categories c ON c.category_id = p.category_id GROUP BY c.name""",
    """SELECT cu.city FROM order_items oi JOIN orders o ON o.order_id = oi.order_id
       JOIN customers cu ON cu.customer_id = o.customer_id""",  # links to the table joined just before
    "SELECT orders.order_id FROM orders JOIN customers ON customers.customer_id = orders.customer_id",
    "SELECT o2.order_id FROM orders o1 JOIN orders o2 ON o2.customer_id = o1.customer_id",
    """SELECT t.items FROM orders o
       JOIN (SELECT order_id, COUNT(*) AS items FROM order_items GROUP BY order_id) AS t ON t.order_id = o.order_id""",
    """SELECT c.full_name FROM customers c LEFT JOIN LATERAL (
           SELECT o.order_date FROM orders o WHERE o.customer_id = c.customer_id LIMIT 1) AS last ON TRUE""",
])
def test_joins_that_link_both_sides_are_allowed(sql):
    assert validate_sql(sql)


@pytest.mark.parametrize("sql", [
    # A. uncorrelated CROSS JOIN LATERAL: every order paired with every order item
    "SELECT o.order_id FROM orders o CROSS JOIN LATERAL (SELECT oi.order_item_id FROM order_items oi) x",
    # B. the same with LEFT JOIN LATERAL ... ON TRUE
    "SELECT o.order_id FROM orders o LEFT JOIN LATERAL (SELECT oi.order_item_id FROM order_items oi) x ON TRUE",
    # C. uncorrelated aggregate over another business table
    "SELECT c.customer_id, x.n FROM customers c CROSS JOIN LATERAL (SELECT COUNT(*) AS n FROM payments p) x",
    # D. only the subquery's own aliases, even through an internal join
    """SELECT o.order_id FROM orders o CROSS JOIN LATERAL (
           SELECT oi.order_item_id FROM order_items oi JOIN products p ON p.product_id = oi.product_id
           WHERE p.product_id = oi.product_id) x""",
    # the outer table appears, but does not filter the inner rows
    "SELECT o.order_id FROM orders o CROSS JOIN LATERAL (SELECT o.customer_id, oi.order_item_id FROM order_items oi) x",
    "SELECT o.order_id FROM orders o CROSS JOIN LATERAL (SELECT oi.order_item_id FROM order_items oi WHERE o.order_id > 0) x",
    "SELECT o.order_id FROM orders o CROSS JOIN LATERAL (SELECT oi.order_id FROM order_items oi WHERE oi.order_id = o.order_id OR TRUE) x",
    # an inner alias that hides the outer one is not correlation
    "SELECT c.customer_id FROM customers c CROSS JOIN LATERAL (SELECT c.customer_id FROM customers c WHERE c.customer_id = c.customer_id) x",
    # correlation cannot be established for a UNION
    """SELECT o.order_id FROM orders o CROSS JOIN LATERAL (
           SELECT oi.order_id FROM order_items oi WHERE oi.order_id = o.order_id UNION SELECT 1) x""",
])
def test_uncorrelated_lateral_subqueries_are_blocked(sql):
    assert_blocked(sql, "no cross joins")


@pytest.mark.parametrize("sql", [
    # B. latest order per customer with LEFT JOIN LATERAL ... ON TRUE
    """SELECT c.customer_id, x.order_date FROM customers c
       LEFT JOIN LATERAL (
           SELECT o.order_date FROM orders o WHERE o.customer_id = c.customer_id
           ORDER BY o.order_date DESC LIMIT 1) x ON TRUE""",
    # C. correlated, with an internal join of its own
    """SELECT c.customer_id, x.amount FROM customers c
       LEFT JOIN LATERAL (
           SELECT p.amount FROM orders o JOIN payments p ON p.order_id = o.order_id
           WHERE o.customer_id = c.customer_id AND p.status = 'completed'
           ORDER BY p.amount DESC LIMIT 1) x ON TRUE""",
    # a correlated aggregate per row
    "SELECT o.order_id, x.n FROM orders o CROSS JOIN LATERAL (SELECT COUNT(*) AS n FROM order_items oi WHERE oi.order_id = o.order_id) x",
])
def test_correlated_lateral_subqueries_are_allowed(sql):
    assert validate_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT version()",
    "SELECT VERSION() AS v",
    "SELECT current_user",
    "SELECT CURRENT_USER()",
    "SELECT session_user",
    "SELECT current_role",
    "SELECT Current_Role AS r",
    "SELECT user",
    "SELECT USER",
    "SELECT current_database()",
    "SELECT current_catalog",
    "SELECT current_schema",
    "SELECT current_schema()",
    "SELECT current_schemas(true)",
    "SELECT c.city FROM customers c WHERE c.full_name = current_user",  # hidden in a filter
    "WITH s AS (SELECT version() AS v) SELECT v FROM s",  # inside a CTE
    "SELECT COUNT(*) AS n, MAX(session_user) FROM orders",  # beside business aggregates
])
def test_database_metadata_is_blocked(sql):
    assert_blocked(sql, "reveals database metadata")


@pytest.mark.parametrize("sql", [
    "SELECT inet_server_addr()",
    "SELECT inet_server_port()",
    "SELECT inet_client_addr()",
    "SELECT inet_client_port()",
    "SELECT pg_backend_pid()",
    "SELECT current_setting('server_version')",
    "SELECT current_query()",
])
def test_other_server_metadata_functions_were_already_rejected(sql):
    with pytest.raises(UnsafeSQLError):
        validate_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT CURRENT_DATE",
    "SELECT CURRENT_TIMESTAMP",
    "SELECT NOW()",
    "SELECT LOCALTIMESTAMP",
    "SELECT COUNT(*) FROM orders o WHERE o.order_date >= CURRENT_DATE - INTERVAL '30 days'",
    "SELECT SUM(oi.quantity * oi.unit_price) AS revenue, AVG(oi.unit_price), MIN(oi.quantity), MAX(oi.quantity) FROM order_items oi",
    'SELECT "user" FROM customers',  # a quoted identifier is a column, not the keyword
    "SELECT s.user FROM (SELECT 1 AS user) AS s",  # so is a qualified one
])
def test_dates_aggregates_and_ordinary_columns_are_not_metadata(sql):
    assert validate_sql(sql)


@pytest.mark.parametrize("sql", [
    "WITH RECURSIVE n AS (SELECT 1 AS i UNION ALL SELECT i + 1 FROM n WHERE i < 12) SELECT i FROM n",
    "with recursive r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT COUNT(*) FROM r",
    "SELECT * FROM (WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n FROM r) SELECT n FROM r) AS s",
])
def test_recursive_ctes_are_blocked(sql):
    assert_blocked(sql, "WITH RECURSIVE is not allowed")


def test_every_benchmark_reference_query_still_passes():
    from tests.benchmark.ground_truth import GROUND_TRUTH_SQL

    for case_id, sql in GROUND_TRUTH_SQL.items():
        assert validate_sql(sql), case_id


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


# --- Application tables (History and Saved Reports) -------------------------------------
# datapilot.analyses and datapilot.saved_reports (database/app_schema.sql) hold DataPilot's own
# data. Generated SQL must never reach them, in any spelling: the allowlist stays exactly the six
# business tables, in public or unqualified. (The database refuses too: datapilot_readonly has no
# access to schema datapilot; see tests/test_database_privileges.py.)

def test_the_allowlist_is_exactly_the_six_business_tables_in_public():
    assert ALLOWED_TABLES == frozenset({"customers", "categories", "products", "orders", "order_items", "payments"})
    assert ALLOWED_SCHEMAS == frozenset({"", "public"})


@pytest.mark.parametrize("sql, reason", [
    ("SELECT * FROM analyses", "Table 'analyses' is not allowed"),
    ("SELECT * FROM saved_reports", "Table 'saved_reports' is not allowed"),
    ("SELECT * FROM datapilot.analyses", "Schema 'datapilot' is not allowed"),
    ("SELECT * FROM datapilot.saved_reports", "Schema 'datapilot' is not allowed"),
    ('SELECT * FROM "analyses"', "Table 'analyses' is not allowed"),
    ('SELECT * FROM "saved_reports"', "Table 'saved_reports' is not allowed"),
    ('SELECT * FROM "datapilot"."analyses"', "Schema 'datapilot' is not allowed"),
    ('SELECT * FROM "datapilot"."saved_reports"', "Schema 'datapilot' is not allowed"),
    ("SELECT * FROM DATAPILOT.ANALYSES", "Schema 'DATAPILOT' is not allowed"),
    ("SELECT * FROM public.analyses", "Table 'analyses' is not allowed"),
    ("SELECT * FROM postgres.datapilot.saved_reports", "Schema 'postgres' is not allowed"),
    ("SELECT a.question FROM orders o JOIN datapilot.analyses a ON a.row_count = o.order_id",
     "Schema 'datapilot' is not allowed"),
    ("SELECT * FROM orders WHERE EXISTS (SELECT 1 FROM datapilot.saved_reports)", "Schema 'datapilot' is not allowed"),
    ("WITH h AS (SELECT * FROM datapilot.analyses) SELECT * FROM h", "Schema 'datapilot' is not allowed"),
    ("SELECT city FROM customers UNION SELECT question FROM analyses", "Table 'analyses' is not allowed"),
    ("SELECT sr.title FROM saved_reports sr JOIN analyses a ON a.id = sr.analysis_id", "is not allowed"),
])
def test_history_and_saved_report_tables_are_never_queryable(sql, reason):
    assert_blocked(sql, reason)


def test_a_cte_named_like_an_app_table_reads_only_business_data():
    # PostgreSQL resolves the name to the CTE, which is built from an allowed table.
    assert validate_sql("WITH analyses AS (SELECT order_id FROM orders) SELECT order_id FROM analyses")


def test_the_sql_prompt_never_mentions_the_app_tables():
    from app.nl_to_sql import SYSTEM_INSTRUCTION

    prompt = SYSTEM_INSTRUCTION.lower()
    assert "analyses" not in prompt and "saved_reports" not in prompt and "datapilot." not in prompt


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
