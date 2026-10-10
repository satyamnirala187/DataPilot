"""The query pipeline: question -> generated SQL -> validated SQL -> rows -> chart + insight.

This module only coordinates the steps and turns their errors into one QueryServiceError.
Generation, validation, execution, chart selection and insights stay in their own modules.
Only SQL approved by the validator is ever passed to the executor.

The insight is optional: if it fails, the result is still returned, with insight = None.

REQUEST_BUDGET_SECONDS is a cooperative budget, not a hard deadline: nothing is interrupted
mid-call. Instead every external call has its own timeout, and a retry or the insight call only
starts if there is still time for it (and everything after it) to finish within the budget.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.chart_selector import Visualization, select_visualization
from app.db_executor import QueryExecutionError, QueryResult, execute_query
from app.insight_service import INSIGHT_TIMEOUT_MS, InsightError, generate_insight
from app.nl_to_sql import SQL_GENERATION_TIMEOUT_MS, SQLGenerationError, generate_sql
from app.sql_validator import UnsafeSQLError, validate_sql

logger = logging.getLogger(__name__)

# Gemini sometimes answers 503 (overloaded). Only fast transient failures (kind "unavailable": 5xx or
# network errors) are retried, twice, waiting 0.5 s and then 1 s. 4xx errors (including 429) and
# timeouts are never retried. This is the only retry layer: the SDK's own retries stay off.
GEMINI_RETRY_DELAYS = (0.5, 1.0)

# Cooperative time budget for one request, below the frontend's 60 s abort.
REQUEST_BUDGET_SECONDS = 45.0
SQL_ATTEMPT_SECONDS = SQL_GENERATION_TIMEOUT_MS / 1000
INSIGHT_SECONDS = INSIGHT_TIMEOUT_MS / 1000
# Planning reserve, not a measured maximum: when deciding whether another Gemini attempt may start,
# keep this much of the budget for the database stage that follows it. The stage's real controls
# are the 5 s connect timeout per connection attempt and the 5 s statement timeout; how long
# connecting can take overall depends on how many addresses the host resolves to, which can change.
DB_STAGE_RESERVE_SECONDS = 20.0
# Small allowance for in-process work (validation, chart selection, serialisation) and clock jitter.
SAFETY_MARGIN_SECONDS = 1.0


class QueryResponse(BaseModel):
    question: str
    sql: str  # the validated SQL that was actually run
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    visualization: Visualization  # chosen by fixed rules, never by the LLM
    insight: str | None  # None when there are no rows or the insight could not be generated


class QueryServiceError(Exception):
    """The pipeline failed. kind says where and why; the message is safe to show to users.

    Kinds: invalid_question, rate_limited, generation_unavailable, generation_timeout, generation_failed,
    unsafe_sql, database_unavailable, query_timeout, query_not_allowed, query_failed.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def run_business_query(
    question: str,
    *,
    generate: Callable[[str], str] = generate_sql,
    validate: Callable[[str], str] = validate_sql,
    execute: Callable[[str], QueryResult] = execute_query,
    summarize: Callable[[str, list[str], list[list[Any]], bool], str] = generate_insight,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> QueryResponse:
    """Answer a business question with data. The keyword arguments exist so tests can swap steps."""
    question = (question or "").strip()
    if not question:
        raise QueryServiceError("invalid_question", "Please enter a question.")
    deadline = clock() + REQUEST_BUDGET_SECONDS

    generated_sql = _generate_with_retry(question, generate, sleep, lambda: deadline - clock())

    try:
        safe_sql = validate(generated_sql)
    except UnsafeSQLError as error:
        logger.warning("Generated SQL rejected by the validator: %s", error)
        raise QueryServiceError("unsafe_sql", "The generated query was rejected by the safety checks.") from None

    try:
        result = execute(safe_sql)
    except QueryExecutionError as error:
        logger.warning("Query execution failed (%s): %s", error.kind, error)
        raise _execution_error(error.kind) from None

    return QueryResponse(
        question=question,
        sql=safe_sql,
        columns=result.columns,
        rows=result.rows,
        row_count=len(result.rows),
        truncated=result.truncated,
        visualization=select_visualization(result.columns, result.rows),
        insight=_optional_insight(question, result, summarize, lambda: deadline - clock()),
    )


def _optional_insight(question: str, result: QueryResult, summarize: Callable[..., str],
                      time_left: Callable[[], float]) -> str | None:
    """The insight, or None. A failure here never turns a successful query into an error."""
    if not result.rows:
        return None  # nothing to summarise, so no Gemini call
    remaining = time_left()
    if remaining < INSIGHT_SECONDS + SAFETY_MARGIN_SECONDS:
        # Not enough time for the insight call to finish; the result matters more.
        logger.warning("Insight skipped (budget): %.1f s left, needs %.0f s",
                       remaining, INSIGHT_SECONDS + SAFETY_MARGIN_SECONDS)
        return None
    try:
        return summarize(question, result.columns, result.rows, result.truncated)
    except InsightError as error:
        logger.warning("Insight skipped (%s): %s", error.kind, error)
    except Exception as error:  # the insight is optional; never let it break the answer
        logger.warning("Insight skipped (unexpected %s)", type(error).__name__)
    return None


def _generate_with_retry(question: str, generate: Callable[[str], str], sleep: Callable[[float], None],
                        time_left: Callable[[], float]) -> str:
    for delay in (*GEMINI_RETRY_DELAYS, None):
        try:
            return generate(question)
        except SQLGenerationError as error:
            # Retry only if the wait, a full attempt and the database reserve still fit in the budget.
            needed = (delay or 0) + SQL_ATTEMPT_SECONDS + DB_STAGE_RESERVE_SECONDS
            if error.kind == "unavailable" and delay is not None and time_left() >= needed:
                logger.info("Gemini unavailable; retrying in %.1f s", delay)
                sleep(delay)
                continue
            logger.warning("SQL generation failed (%s): %s", error.kind, error)
            raise _generation_error(error.kind) from None
    raise AssertionError("unreachable")


def _generation_error(kind: str) -> QueryServiceError:
    if kind == "invalid_question":
        return QueryServiceError("invalid_question", "Please enter a question.")
    if kind == "rate_limited":
        return QueryServiceError("rate_limited", "Too many requests to the AI service. Please try again shortly.")
    if kind == "timeout":
        return QueryServiceError("generation_timeout", "The AI service took too long to respond. Please try again.")
    if kind in ("unavailable", "not_configured", "model_unavailable"):
        return QueryServiceError("generation_unavailable", "The AI service is unavailable right now. Please try again later.")
    # request_failed, empty_response, invalid_response, or anything new
    return QueryServiceError("generation_failed", "The AI service could not answer this question. Please rephrase it.")


def _execution_error(kind: str) -> QueryServiceError:
    if kind == "unavailable":
        return QueryServiceError("database_unavailable", "The database is unavailable right now. Please try again later.")
    if kind == "timeout":
        return QueryServiceError("query_timeout", "The query took too long to run. Try a narrower question.")
    if kind == "permission_denied":
        return QueryServiceError("query_not_allowed", "The query was refused by the database.")
    return QueryServiceError("query_failed", "The generated query could not be run. Please rephrase your question.")
