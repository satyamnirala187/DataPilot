"""Store each successful analysis as History, in datapilot.analyses (database/app_schema.sql).

What is stored is a snapshot of exactly what the user saw: the question, the SQL that ran, the
columns and rows, the chart spec and the insight. Reopening History later reads this snapshot; it
never reruns Gemini or the SQL.

This module only writes. It connects with APP_DATABASE_URL as the role datapilot_app (SELECT and
INSERT on the two History tables, nothing on the business tables), over TLS, with short timeouts.
It never uses READONLY_DATABASE_URL or the admin DATABASE_URL, and never runs generated SQL: its
only statement is a fixed INSERT whose values are bound parameters.

History is secondary to the answer, so record_analysis() never raises. It returns a HistoryResult:
  saved     the snapshot was stored; analysis_id is its UUID
  disabled  APP_DATABASE_URL is not configured, so nothing was attempted
  failed    storing did not work; error is a fixed kind (unavailable, timeout, insert_failed,
            invalid_configuration) or, for anything unexpected, the exception's class name
Errors never contain connection details, SQL, rows or the question.
"""

from dataclasses import dataclass
from typing import Literal

import psycopg
from psycopg.types.json import Jsonb

from app.config import settings
from app.db_executor import tls_sslmode
from app.query_service import QueryResponse

# Applies to each connection attempt (one per address the host resolves to), like the executor's.
CONNECT_TIMEOUT_SECONDS = 5
# The INSERT is one small row; anything slower than this is a problem, not a big result.
STATEMENT_TIMEOUT_MS = 3000
# The shape of the stored snapshot. Bump it if the stored fields ever change meaning.
SNAPSHOT_VERSION = 1

INSERT_ANALYSIS = """
    INSERT INTO datapilot.analyses
        (account_id, question, generated_sql, result_columns, result_rows, row_count, truncated,
         visualization, insight, snapshot_version)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id
"""


@dataclass(frozen=True)
class HistoryResult:
    status: Literal["saved", "disabled", "failed"]
    analysis_id: str | None = None
    error: str | None = None  # safe to log: a fixed kind or an exception class name


DISABLED = HistoryResult("disabled")


def record_analysis(response: QueryResponse, *, account_id: str) -> HistoryResult:
    """Store one successful analysis for account_id. Never raises: History must not cost the user a
    valid answer."""
    try:
        return _record(response, account_id)
    except Exception as error:  # anything not anticipated below; only the class name is kept
        return HistoryResult("failed", error=type(error).__name__)


def _record(response: QueryResponse, account_id: str) -> HistoryResult:
    if settings.app_database_url is None:
        return DISABLED
    url = settings.app_database_url.get_secret_value()

    try:
        sslmode = tls_sslmode(url)
    except psycopg.Error:  # a malformed URL; its text is never passed on
        return HistoryResult("failed", error="invalid_configuration")
    try:
        conn = psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_SECONDS, sslmode=sslmode)
    except psycopg.Error:
        # Connection errors can include host and user names, so none of it is kept.
        return HistoryResult("failed", error="unavailable")

    try:
        with conn, conn.transaction():
            conn.execute("SELECT set_config('statement_timeout', %s, true)", (str(STATEMENT_TIMEOUT_MS),))
            analysis_id = conn.execute(INSERT_ANALYSIS, _snapshot(response, account_id)).fetchone()[0]
    except psycopg.errors.QueryCanceled:
        return HistoryResult("failed", error="timeout")
    except psycopg.OperationalError:
        return HistoryResult("failed", error="unavailable")
    except psycopg.Error:
        # Refused by the database: a constraint (e.g. an over-long question), a privilege, ...
        return HistoryResult("failed", error="insert_failed")
    return HistoryResult("saved", analysis_id=str(analysis_id))


def _snapshot(response: QueryResponse, account_id: str) -> tuple:
    """The INSERT's parameters, in INSERT_ANALYSIS's column order. JSON values are bound as Jsonb,
    never built into the SQL text."""
    return (
        account_id,
        response.question,
        response.sql,
        Jsonb(response.columns),
        Jsonb(response.rows),
        response.row_count,
        response.truncated,
        Jsonb(response.visualization.model_dump()),
        response.insight,
        SNAPSHOT_VERSION,
    )
