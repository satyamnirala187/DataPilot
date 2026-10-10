"""Run already-approved SQL against PostgreSQL as the read-only role.

This module only executes. It does not validate SQL (that is app.sql_validator's job) and
contains no business logic. Every query runs:
  - as the datapilot_readonly role (READONLY_DATABASE_URL),
  - inside a read-only transaction that is always rolled back,
  - with a short statement timeout,
  - returning at most max_result_rows rows, and at most max_result_bytes of JSON.
Errors are reported without connection details.

The byte limit stops DataPilot from serialising and returning an oversized result. It cannot stop
PostgreSQL or the driver from building a large value first (psycopg has already received the
rows, and each row is converted before it is measured): that is bounded by the validator (no
amplifying functions, cross joins or recursion), the statement timeout and the row limit.
"""

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg

from app.config import settings

# Applies to each psycopg connection attempt separately (one attempt per address the host
# resolves to), so it bounds every attempt, not the total time spent connecting.
CONNECT_TIMEOUT_SECONDS = 5


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]  # JSON-friendly values only
    truncated: bool  # True if the query had more rows than were returned


class QueryExecutionError(Exception):
    """A query could not be run. kind is one of: unavailable, timeout, permission_denied, query_error,
    result_too_large.

    The message never contains credentials or connection details.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def execute_query(sql: str, *, database_url: str | None = None, timeout_ms: int | None = None,
                  max_rows: int | None = None, max_bytes: int | None = None) -> QueryResult:
    """Run one approved SELECT and return its columns and rows."""
    url = database_url or _readonly_url()
    timeout_ms = timeout_ms or settings.query_timeout_ms
    max_rows = max_rows or settings.max_result_rows
    max_bytes = max_bytes or settings.max_result_bytes

    try:
        conn = psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    except psycopg.Error:
        # Connection errors can include host and user names, so none of it is passed on.
        raise QueryExecutionError("unavailable", "Could not connect to the database.") from None

    try:
        with conn:
            conn.read_only = True  # every transaction on this connection is READ ONLY
            with conn.transaction(force_rollback=True):  # never commit anything
                conn.execute("SELECT set_config('statement_timeout', %s, true)", (str(timeout_ms),))
                cursor = conn.execute(sql)
                if cursor.description is None:  # not a query that returns rows
                    return QueryResult(columns=[], rows=[], truncated=False)
                columns = [column.name for column in cursor.description]
                rows = cursor.fetchmany(max_rows + 1)
    except psycopg.errors.QueryCanceled:
        raise QueryExecutionError("timeout", f"The query took longer than {timeout_ms} ms.") from None
    except (psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction):
        raise QueryExecutionError("permission_denied", "The query tried to do something that is not allowed.") from None
    except psycopg.OperationalError:
        raise QueryExecutionError("unavailable", "The database connection failed.") from None
    except psycopg.Error as error:
        # SQL errors (unknown column, bad syntax, ...) describe the query, not the connection.
        message = error.diag.message_primary or "The query failed."
        raise QueryExecutionError("query_error", message) from None

    truncated = len(rows) > max_rows
    return QueryResult(columns=columns, rows=_json_rows(columns, rows[:max_rows], max_bytes), truncated=truncated)


def _json_rows(columns: list[str], rows: list[tuple], max_bytes: int) -> list[list[Any]]:
    """Convert rows to JSON-friendly values, or refuse the whole result if the JSON of its columns
    and rows would exceed max_bytes. Never returns part of a result."""
    size = json_size(columns, max_bytes) + 2  # the rows array's brackets
    converted = []
    for row in rows:
        values = [to_json_value(value) for value in row]
        size += json_size(values, max_bytes - size) + (1 if converted else 0)  # comma between rows
        if size > max_bytes:
            break
        converted.append(values)
    if size > max_bytes:
        raise QueryExecutionError("result_too_large", "The result is larger than the size limit.")
    return converted


def json_size(value: Any, limit: int) -> int:
    """Bytes `value` takes as compact UTF-8 JSON, counting no further once the total passes limit.

    A string longer than the remaining budget is counted by its length alone (every character is
    at least one byte), so it is never JSON-encoded just to be measured; shorter strings are
    encoded one at a time, a temporary copy bounded by the budget."""
    if isinstance(value, str):
        if len(value) > limit:  # every character takes at least one byte
            return len(value)
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    if isinstance(value, (list, dict)):
        pairs = value.items() if isinstance(value, dict) else ((None, item) for item in value)
        total = 2  # brackets or braces
        for index, (key, item) in enumerate(pairs):
            total += 1 if index else 0  # comma
            if key is not None:
                total += json_size(str(key), limit - total) + 1  # key and colon
            total += json_size(item, limit - total)
            if total > limit:
                break
        return total
    return len(json.dumps(value))  # numbers, booleans and None


def _readonly_url() -> str:
    if settings.readonly_database_url is None:
        raise QueryExecutionError("unavailable", "READONLY_DATABASE_URL is not configured.")
    return settings.readonly_database_url.get_secret_value()


def to_json_value(value: Any) -> Any:
    """Convert a value returned by psycopg into something json.dumps() accepts."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)  # e.g. "365 days, 0:00:00" for an interval
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, (list, tuple)):
        return [to_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_json_value(item) for key, item in value.items()}
    return str(value)
