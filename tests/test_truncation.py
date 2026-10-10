"""The `truncated` flag: true only when DataPilot's own 500-row cap left rows out.

Each test runs the real validator and the real executor against a fake database that applies the
query's LIMIT like PostgreSQL would. No live database, no Gemini.
"""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
import sqlglot

from app import db_executor
from app.config import Settings
from app.db_executor import execute_query
from app.query_service import run_business_query
from app.sql_validator import MAX_LIMIT, UnsafeSQLError, validate_sql


class FakeDatabase:
    """Holds `total` matching rows and returns as many as the SQL's LIMIT allows."""

    def __init__(self, total):
        self.rows = [[i] for i in range(1, total + 1)]
        self.queries = []

    def connect(self, url, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transaction(self, force_rollback=False):
        return nullcontext()

    def execute(self, sql, params=None):
        if params is not None:  # the per-transaction statement_timeout
            return SimpleNamespace(description=None)
        self.queries.append(sql)
        limit = sqlglot.parse_one(sql, read="postgres").args.get("limit")
        returned = self.rows if limit is None else self.rows[: int(limit.expression.name)]
        return SimpleNamespace(description=[SimpleNamespace(name="order_id")], fetchmany=lambda n: returned[:n])


def run(monkeypatch, sql, total):
    database = FakeDatabase(total)
    monkeypatch.setattr(db_executor.psycopg, "connect", database.connect)
    result = execute_query(validate_sql(sql), database_url="postgresql://fake", max_rows=MAX_LIMIT)
    return result, database


NO_LIMIT = "SELECT order_id FROM orders"


@pytest.mark.parametrize("sql, total, rows, truncated", [
    (NO_LIMIT, 499, 499, False),                          # fewer than 500 rows
    (NO_LIMIT, 500, 500, False),                          # exactly 500 rows: nothing left out
    (NO_LIMIT, 501, 500, True),                           # one row more than the cap
    (NO_LIMIT, 9241, 500, True),                          # far more than the cap
    (NO_LIMIT + " LIMIT 10", 9241, 10, False),            # the question asked for 10
    (NO_LIMIT + " LIMIT 500", 9241, 500, False),          # the question asked for exactly 500
    (NO_LIMIT + " LIMIT 501", 9241, 500, True),           # asked for 501; DataPilot returned 500
    (NO_LIMIT + " LIMIT 100000", 9241, 500, True),        # capped; more rows existed
    (NO_LIMIT + " LIMIT 100000", 450, 450, False),        # capped, but every row fits
    (NO_LIMIT + " LIMIT 100000", 500, 500, False),        # capped, exactly 500 exist
])
def test_truncated_only_when_the_safety_cap_left_rows_out(monkeypatch, sql, total, rows, truncated):
    result, _ = run(monkeypatch, sql, total)
    assert len(result.rows) == rows
    assert result.truncated is truncated


def test_never_more_than_500_rows_are_returned(monkeypatch):
    for sql in (NO_LIMIT, NO_LIMIT + " LIMIT 501", NO_LIMIT + " LIMIT 1000000"):
        result, _ = run(monkeypatch, sql, 9241)
        assert len(result.rows) == MAX_LIMIT


def test_one_query_and_no_count(monkeypatch):
    _, database = run(monkeypatch, NO_LIMIT, 9241)
    assert len(database.queries) == 1
    assert "COUNT" not in database.queries[0].upper()


def test_the_pipeline_reports_rows_and_truncation(monkeypatch):
    database = FakeDatabase(9241)
    monkeypatch.setattr(db_executor.psycopg, "connect", database.connect)
    response = run_business_query(
        "All order ids", generate=lambda q: NO_LIMIT, summarize=lambda *a: None,
        execute=lambda sql: execute_query(sql, database_url="postgresql://fake", max_rows=MAX_LIMIT),
    )
    assert response.row_count == MAX_LIMIT and response.truncated is True
    assert response.sql == database.queries[0] == f"{NO_LIMIT} LIMIT {MAX_LIMIT + 1}"


def test_validator_cap_and_executor_row_cap_agree():
    # The probe row only works if both sides use the same cap.
    assert Settings.model_fields["max_result_rows"].default == MAX_LIMIT


@pytest.mark.parametrize("sql", [
    "SELECT order_id FROM orders LIMIT (SELECT 10)",
    "SELECT order_id FROM orders FETCH FIRST 5 ROWS ONLY",
    "DELETE FROM orders",
    "SELECT order_id FROM orders; DROP TABLE orders",
])
def test_limit_safety_rules_are_unchanged(sql):
    with pytest.raises(UnsafeSQLError):
        validate_sql(sql)
