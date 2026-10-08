"""The query pipeline: question -> generated SQL -> validated SQL -> rows.

This module only coordinates the three existing steps and turns their errors into one
QueryServiceError. Generation, validation and execution stay in their own modules.
Only SQL approved by the validator is ever passed to the executor.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.db_executor import QueryExecutionError, QueryResult, execute_query
from app.nl_to_sql import SQLGenerationError, generate_sql
from app.sql_validator import UnsafeSQLError, validate_sql

logger = logging.getLogger(__name__)

# Gemini sometimes answers 503 (overloaded). Only transient failures (kind "unavailable": 5xx or
# network errors) are retried, twice, waiting 0.5 s and then 1 s. 4xx errors are never retried.
GEMINI_RETRY_DELAYS = (0.5, 1.0)


class QueryResponse(BaseModel):
    question: str
    sql: str  # the validated SQL that was actually run
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool


class QueryServiceError(Exception):
    """The pipeline failed. kind says where and why; the message is safe to show to users.

    Kinds: invalid_question, rate_limited, generation_unavailable, generation_failed,
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
    sleep: Callable[[float], None] = time.sleep,
) -> QueryResponse:
    """Answer a business question with data. The keyword arguments exist so tests can swap steps."""
    question = (question or "").strip()
    if not question:
        raise QueryServiceError("invalid_question", "Please enter a question.")

    generated_sql = _generate_with_retry(question, generate, sleep)

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
    )


def _generate_with_retry(question: str, generate: Callable[[str], str], sleep: Callable[[float], None]) -> str:
    for delay in (*GEMINI_RETRY_DELAYS, None):
        try:
            return generate(question)
        except SQLGenerationError as error:
            if error.kind == "unavailable" and delay is not None:
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
