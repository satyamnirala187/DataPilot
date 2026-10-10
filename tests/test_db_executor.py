"""Tests for the read-only database executor (backend/app/db_executor.py).

The unit tests need no database. The integration tests run against the real database as the
read-only role and are skipped when READONLY_DATABASE_URL is not configured.
"""

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.config import settings
from app.db_executor import QueryExecutionError, execute_query, to_json_value

# --- JSON conversion (no database) -----------------------------------------------------


@pytest.mark.parametrize("value, expected", [
    (None, None),
    (True, True),
    (42, 42),
    ("Mumbai", "Mumbai"),
    (1.5, 1.5),
    (Decimal("5000"), 5000),
    (Decimal("5000.00"), 5000),
    (Decimal("21304631.99"), 21304631.99),
    (date(2026, 9, 30), "2026-09-30"),
    (datetime(2026, 9, 30, 14, 5, 0), "2026-09-30T14:05:00"),
    (time(9, 30), "09:30:00"),
    (timedelta(days=365), "365 days, 0:00:00"),
    (UUID("12345678-1234-5678-1234-567812345678"), "12345678-1234-5678-1234-567812345678"),
    (b"\x01\xff", "01ff"),
    ([Decimal("1.5"), date(2026, 1, 1)], [1.5, "2026-01-01"]),
    ({"total": Decimal("10")}, {"total": 10}),
])
def test_values_are_converted_to_json_friendly_types(value, expected):
    assert to_json_value(value) == expected


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), float("nan"), float("inf")])
def test_non_finite_numbers_become_null(value):
    assert to_json_value(value) is None


def test_converted_values_are_json_serialisable():
    row = [Decimal("12.34"), date(2026, 9, 30), timedelta(hours=5), None, [Decimal("1")]]
    json.dumps([to_json_value(v) for v in row])


def test_unreachable_database_error_hides_connection_details():
    secret_url = "postgresql://someone:supersecretpassword@127.0.0.1:1/nowhere"
    with pytest.raises(QueryExecutionError) as caught:
        execute_query("SELECT 1", database_url=secret_url)
    assert caught.value.kind == "unavailable"
    message = str(caught.value)
    assert "supersecretpassword" not in message and "someone" not in message and "127.0.0.1" not in message


# --- Against the real database, as the read-only role ----------------------------------

needs_database = pytest.mark.skipif(settings.readonly_database_url is None,
                                    reason="READONLY_DATABASE_URL is not configured")


@needs_database
def test_count_query_returns_columns_and_rows():
    result = execute_query("SELECT COUNT(*) AS customers FROM customers")
    assert result.columns == ["customers"]
    assert result.rows == [[1000]]
    assert result.truncated is False


@needs_database
def test_decimals_and_dates_come_back_json_friendly():
    result = execute_query("SELECT price, cost, DATE '2026-09-30' AS day FROM products ORDER BY product_id LIMIT 3")
    assert result.columns == ["price", "cost", "day"]
    for price, cost, day in result.rows:
        assert isinstance(price, (int, float)) and isinstance(cost, (int, float))
        assert day == "2026-09-30"
    json.dumps(result.rows)


@needs_database
def test_rows_are_capped_and_flagged_as_truncated():
    result = execute_query("SELECT order_item_id FROM order_items", max_rows=10)
    assert len(result.rows) == 10
    assert result.truncated is True


@needs_database
def test_validated_query_over_the_cap_is_flagged_as_truncated():
    # The real database, the real validator: order_items has far more than 500 rows.
    from app.sql_validator import MAX_LIMIT, validate_sql
    result = execute_query(validate_sql("SELECT order_item_id FROM order_items"))
    assert len(result.rows) == MAX_LIMIT
    assert result.truncated is True


@needs_database
def test_validated_query_with_a_small_limit_is_not_truncated():
    from app.sql_validator import validate_sql
    result = execute_query(validate_sql("SELECT order_item_id FROM order_items LIMIT 10"))
    assert len(result.rows) == 10
    assert result.truncated is False


@needs_database
def test_result_exactly_at_the_cap_is_not_truncated():
    result = execute_query("SELECT category_id FROM categories", max_rows=10)
    assert len(result.rows) == 10
    assert result.truncated is False


@needs_database
def test_slow_query_is_cancelled_by_the_statement_timeout():
    with pytest.raises(QueryExecutionError) as caught:
        execute_query("SELECT pg_sleep(3)", timeout_ms=300)
    assert caught.value.kind == "timeout"


@needs_database
@pytest.mark.parametrize("sql", [
    "INSERT INTO categories (name) VALUES ('should never exist')",
    "UPDATE products SET price = 0",
    "DELETE FROM payments",
    "TRUNCATE orders",
    "DROP TABLE customers",
    "CREATE TABLE stolen AS SELECT * FROM customers",
    "SELECT nextval('orders_order_id_seq')",
])
def test_writes_are_refused_even_without_the_validator(sql):
    with pytest.raises(QueryExecutionError) as caught:
        execute_query(sql)
    assert caught.value.kind == "permission_denied"


@needs_database
def test_data_is_unchanged_after_write_attempts():
    result = execute_query("""
        SELECT (SELECT COUNT(*) FROM categories WHERE name = 'should never exist'),
               (SELECT COUNT(*) FROM products WHERE price = 0),
               (SELECT COUNT(*) FROM payments),
               (SELECT COUNT(*) FROM orders)
    """)
    assert result.rows == [[0, 0, 5257, 5000]]


@needs_database
def test_tables_outside_the_six_are_not_readable():
    with pytest.raises(QueryExecutionError) as caught:
        execute_query("SELECT COUNT(*) FROM auth.users")
    assert caught.value.kind == "permission_denied"


@needs_database
def test_sql_errors_are_reported_without_connection_details():
    with pytest.raises(QueryExecutionError) as caught:
        execute_query("SELECT no_such_column FROM customers")
    assert caught.value.kind == "query_error"
    assert "no_such_column" in str(caught.value)
    assert "supabase" not in str(caught.value).lower() and "@" not in str(caught.value)


def test_secret_url_is_hidden_in_settings_repr():
    assert "postgresql" not in repr(settings) and "postgresql" not in str(settings.readonly_database_url)
