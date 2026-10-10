"""History and Saved Reports: DataPilot's own data, in schema datapilot (database/app_schema.sql).

  datapilot.analyses       one row per successful answer: a snapshot of exactly what the user saw
                           (question, SQL, columns and rows, chart spec, insight)
  datapilot.saved_reports  an analysis the user chose to keep, with a title; it points to the
                           analysis instead of copying it (at most one report per analysis)

Reopening History or a saved report reads the stored snapshot. Nothing here calls Gemini,
regenerates SQL or runs a stored query: the stored SQL is only data that is shown again.

This module connects with APP_DATABASE_URL as the role datapilot_app (SELECT and INSERT on those two
tables, nothing on the business tables), over TLS, with short timeouts. It never uses
READONLY_DATABASE_URL or the admin DATABASE_URL, and never runs generated SQL: every statement is
fixed text with bound parameters. Data always belongs to an account (today the one shared demo
account, auth.DEMO_ACCOUNT_ID); a saved report's account is its analysis's, so every report query
joins through datapilot.analyses.

Two error contracts:
  record_analysis()  History is secondary to the answer, so it never raises. It returns a
                     HistoryResult: saved (with analysis_id), disabled (APP_DATABASE_URL not set) or
                     failed (with a safe error kind).
  the read and save operations  The History API needs the database, so they raise
                     HistoryUnavailable (with a safe kind) when it cannot be used, return None when
                     the analysis or report does not exist for the account, and save_report raises
                     AlreadySaved for an analysis that already has a report.
Error kinds: disabled, invalid_configuration, unavailable, timeout, insert_failed, query_failed,
invalid_record, or for anything unexpected the exception's class name. None of them contains
connection details, SQL, rows or the question.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ValidationError

from app.chart_selector import Visualization
from app.config import settings
from app.db_executor import tls_sslmode
from app.query_service import QueryResponse

# Applies to each connection attempt (one per address the host resolves to), like the executor's.
CONNECT_TIMEOUT_SECONDS = 5
# Every statement here touches a few small rows by key or index; anything slower is a problem.
STATEMENT_TIMEOUT_MS = 3000
# The shape of the stored snapshot. Bump it if the stored fields ever change meaning.
SNAPSHOT_VERSION = 1

VisualizationType = Visualization.model_fields["type"].annotation  # kpi, bar, line or table

INSERT_ANALYSIS = """
    INSERT INTO datapilot.analyses
        (account_id, question, generated_sql, result_columns, result_rows, row_count, truncated,
         visualization, insight, snapshot_version)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id
"""

# The stored snapshot of an analysis (alias a), as the user saw it.
ANALYSIS_COLUMNS = """a.id, a.question, a.generated_sql, a.result_columns, a.result_rows, a.row_count,
           a.truncated, a.visualization, a.insight, a.created_at"""

LIST_HISTORY = """
    SELECT a.id, a.question, a.visualization ->> 'type' AS visualization_type, a.row_count, a.truncated,
           a.created_at, r.id AS report_id
    FROM datapilot.analyses AS a
    LEFT JOIN datapilot.saved_reports AS r ON r.analysis_id = a.id
    WHERE a.account_id = %s
    ORDER BY a.created_at DESC, a.id DESC
    LIMIT %s
"""

GET_ANALYSIS = f"""
    SELECT {ANALYSIS_COLUMNS}, r.id AS report_id, r.title AS report_title
    FROM datapilot.analyses AS a
    LEFT JOIN datapilot.saved_reports AS r ON r.analysis_id = a.id
    WHERE a.id = %s AND a.account_id = %s
"""

# Saves only an analysis of this account; a second save of the same analysis inserts nothing
# (UNIQUE (analysis_id)). DO NOTHING needs no UPDATE privilege.
SAVE_REPORT = """
    INSERT INTO datapilot.saved_reports (analysis_id, title)
    SELECT a.id, %s FROM datapilot.analyses AS a WHERE a.id = %s AND a.account_id = %s
    ON CONFLICT (analysis_id) DO NOTHING
    RETURNING id AS report_id, analysis_id, title AS report_title, saved_at
"""

ANALYSIS_EXISTS = "SELECT EXISTS (SELECT 1 FROM datapilot.analyses WHERE id = %s AND account_id = %s)"

LIST_SAVED_REPORTS = """
    SELECT r.id AS report_id, r.title AS report_title, r.saved_at, a.id, a.question,
           a.visualization ->> 'type' AS visualization_type, a.row_count, a.truncated, a.created_at
    FROM datapilot.saved_reports AS r
    JOIN datapilot.analyses AS a ON a.id = r.analysis_id
    WHERE a.account_id = %s
    ORDER BY r.saved_at DESC, r.id DESC
    LIMIT %s
"""

GET_SAVED_REPORT = f"""
    SELECT r.id AS report_id, r.title AS report_title, r.saved_at, {ANALYSIS_COLUMNS}
    FROM datapilot.saved_reports AS r
    JOIN datapilot.analyses AS a ON a.id = r.analysis_id
    WHERE r.id = %s AND a.account_id = %s
"""


# --- What the API returns ----------------------------------------------------------------------
# Field names follow QueryResponse (sql, columns, rows), so the frontend can show a stored analysis
# with the same components as a fresh answer. account_id and snapshot_version are never returned.

class StoredAnalysis(BaseModel):
    id: UUID
    question: str
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    visualization: Visualization  # the stored spec, validated; chart selection is never rerun
    insight: str | None
    created_at: datetime


class SavedReportRef(BaseModel):
    id: UUID
    title: str


class AnalysisDetail(StoredAnalysis):
    saved_report: SavedReportRef | None


class AnalysisSummary(BaseModel):
    id: UUID
    question: str
    visualization_type: VisualizationType
    row_count: int
    truncated: bool
    created_at: datetime
    saved_report_id: UUID | None


class SavedReport(BaseModel):
    id: UUID
    analysis_id: UUID
    title: str
    saved_at: datetime


class SavedReportSummary(BaseModel):
    id: UUID
    analysis_id: UUID
    title: str
    question: str
    visualization_type: VisualizationType
    row_count: int
    truncated: bool
    saved_at: datetime
    analysis_created_at: datetime


class SavedReportDetail(BaseModel):
    id: UUID
    title: str
    saved_at: datetime
    analysis: StoredAnalysis



# --- Results and errors ------------------------------------------------------------------------

@dataclass(frozen=True)
class HistoryResult:
    """What record_analysis did."""

    status: Literal["saved", "disabled", "failed"]
    analysis_id: str | None = None
    error: str | None = None  # safe to log: a fixed kind or an exception class name


DISABLED = HistoryResult("disabled")


class HistoryUnavailable(Exception):
    """The History database could not be used. kind is safe to log; nothing else is kept."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class AlreadySaved(Exception):
    """The analysis already has a saved report (one per analysis)."""


# --- Writing History (POST /query) --------------------------------------------------------------

def record_analysis(response: QueryResponse, *, account_id: str) -> HistoryResult:
    """Store one successful answer for account_id. Never raises: History must not cost the user a
    valid answer."""
    try:
        with _transaction(error_kind="insert_failed") as conn:
            analysis_id = conn.execute(INSERT_ANALYSIS, _snapshot(response, account_id)).fetchone()[0]
    except HistoryUnavailable as error:
        return DISABLED if error.kind == "disabled" else HistoryResult("failed", error=error.kind)
    except Exception as error:  # anything not anticipated; only the class name is kept
        return HistoryResult("failed", error=type(error).__name__)
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


# --- The History and Saved Reports API -----------------------------------------------------------

def list_history(account_id: str, limit: int) -> list[AnalysisSummary]:
    """The account's analyses, newest first: summaries only, no rows or SQL."""
    with _transaction(read_only=True) as conn:
        rows = conn.cursor(row_factory=dict_row).execute(LIST_HISTORY, (account_id, limit)).fetchall()
        return [AnalysisSummary(id=row["id"], question=row["question"], visualization_type=row["visualization_type"],
                                row_count=row["row_count"], truncated=row["truncated"], created_at=row["created_at"],
                                saved_report_id=row["report_id"]) for row in rows]


def get_analysis(account_id: str, analysis_id: UUID) -> AnalysisDetail | None:
    """One stored analysis of the account, with its saved report if any; None if there is none."""
    with _transaction(read_only=True) as conn:
        row = conn.cursor(row_factory=dict_row).execute(GET_ANALYSIS, (analysis_id, account_id)).fetchone()
        if row is None:
            return None
        report = SavedReportRef(id=row["report_id"], title=row["report_title"]) if row["report_id"] else None
        return AnalysisDetail(**_stored_analysis(row).model_dump(), saved_report=report)


def save_report(account_id: str, analysis_id: UUID, title: str) -> SavedReport | None:
    """Save the account's analysis under title. None if the account has no such analysis; raises
    AlreadySaved if it already has a report. Only the id and title are written: the report's content
    is the stored analysis."""
    with _transaction(error_kind="insert_failed") as conn:
        cursor = conn.cursor(row_factory=dict_row)
        row = cursor.execute(SAVE_REPORT, (title, analysis_id, account_id)).fetchone()
        if row is not None:
            return SavedReport(id=row["report_id"], analysis_id=row["analysis_id"], title=row["report_title"],
                               saved_at=row["saved_at"])
        # Nothing was inserted: the account has no such analysis, or it is already saved.
        already_saved = cursor.execute(ANALYSIS_EXISTS, (analysis_id, account_id)).fetchone()["exists"]
    if already_saved:
        raise AlreadySaved()
    return None


def list_saved_reports(account_id: str, limit: int) -> list[SavedReportSummary]:
    """The account's saved reports, most recently saved first: summaries only, no rows or SQL."""
    with _transaction(read_only=True) as conn:
        rows = conn.cursor(row_factory=dict_row).execute(LIST_SAVED_REPORTS, (account_id, limit)).fetchall()
        return [SavedReportSummary(id=row["report_id"], analysis_id=row["id"], title=row["report_title"],
                                   question=row["question"], visualization_type=row["visualization_type"],
                                   row_count=row["row_count"], truncated=row["truncated"], saved_at=row["saved_at"],
                                   analysis_created_at=row["created_at"]) for row in rows]


def get_saved_report(account_id: str, report_id: UUID) -> SavedReportDetail | None:
    """One saved report of the account, with its full analysis; None if there is none."""
    with _transaction(read_only=True) as conn:
        row = conn.cursor(row_factory=dict_row).execute(GET_SAVED_REPORT, (report_id, account_id)).fetchone()
        if row is None:
            return None
        return SavedReportDetail(id=row["report_id"], title=row["report_title"], saved_at=row["saved_at"],
                                 analysis=_stored_analysis(row))


def _stored_analysis(row: dict) -> StoredAnalysis:
    """Database names to API names. The model validates what was stored (the chart spec against
    Visualization, columns as strings, rows as lists), so a malformed record is refused, not shown."""
    return StoredAnalysis(id=row["id"], question=row["question"], sql=row["generated_sql"],
                          columns=row["result_columns"], rows=row["result_rows"], row_count=row["row_count"],
                          truncated=row["truncated"], visualization=row["visualization"], insight=row["insight"],
                          created_at=row["created_at"])


# --- One connection, one short transaction ---------------------------------------------------------

@contextmanager
def _transaction(*, read_only: bool = False, error_kind: str = "query_failed") -> Iterator[psycopg.Connection]:
    """A connection as datapilot_app over TLS, inside one transaction that commits if the block
    succeeds and rolls back otherwise. Every failure becomes HistoryUnavailable with a safe kind;
    error_kind is used when the database refuses a statement."""
    if settings.app_database_url is None:
        raise HistoryUnavailable("disabled")
    url = settings.app_database_url.get_secret_value()
    try:
        sslmode = tls_sslmode(url)
    except psycopg.Error:  # a malformed URL; its text is never passed on
        raise HistoryUnavailable("invalid_configuration") from None
    try:
        conn = psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_SECONDS, sslmode=sslmode)
    except psycopg.Error:
        # Connection errors can include host and user names, so none of it is kept.
        raise HistoryUnavailable("unavailable") from None

    try:
        with conn:
            conn.read_only = read_only  # reads also run in a READ ONLY transaction
            with conn.transaction():
                conn.execute("SELECT set_config('statement_timeout', %s, true)", (str(STATEMENT_TIMEOUT_MS),))
                yield conn
    except psycopg.errors.QueryCanceled:
        raise HistoryUnavailable("timeout") from None
    except psycopg.OperationalError:
        raise HistoryUnavailable("unavailable") from None
    except psycopg.Error:
        # Refused by the database: a constraint (e.g. an over-long question), a privilege, ...
        raise HistoryUnavailable(error_kind) from None
    except ValidationError:  # a stored record that does not match the snapshot's shape
        raise HistoryUnavailable("invalid_record") from None
    except Exception as error:  # anything unexpected; only the class name is kept
        raise HistoryUnavailable(type(error).__name__) from None
