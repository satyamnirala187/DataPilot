"""The query pipeline: question -> generated SQL -> validated SQL -> rows -> chart + insight.

This module only coordinates the steps and turns their errors into one QueryServiceError.
Generation, validation, execution, chart selection and insights stay in their own modules.
Only SQL approved by the validator is ever passed to the executor.

SQL comes from Gemini (app.nl_to_sql). When GROQ_API_KEY is set, GroqCloud (app.groq_sql) is the
fallback for Gemini failures that say nothing about the question (FALLBACK_KINDS). Whichever
provider wrote it, the SQL goes through the same validator and the same read-only executor. There
is never a fallback after the validator or the database has seen the SQL: a rejected query is final.

The insight is optional: if it fails, the result is still returned, with insight = None.

Each step records its outcome and timing in a QueryMetrics object (app.request_log), which is
logged as one summary line per request. Nothing here logs the question, the SQL or the rows.

REQUEST_BUDGET_SECONDS is a cooperative budget, not a hard deadline: nothing is interrupted
mid-call. Instead every external call has its own timeout, and a retry or the insight call only
starts if there is still time for it (and everything after it) to finish within the budget.
"""

import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app import groq_sql
from app.chart_selector import Visualization, select_visualization
from app.config import settings
from app.db_executor import QueryExecutionError, QueryResult, execute_query
from app.groq_sql import GROQ_SQL_TIMEOUT_MS
from app.insight_service import INSIGHT_TIMEOUT_MS, InsightError, generate_insight
from app.nl_to_sql import SQL_GENERATION_TIMEOUT_MS, SQLGenerationError, generate_sql
from app.request_log import QueryMetrics, new_request_id
from app.sql_validator import UnsafeSQLError, validate_sql

# Gemini sometimes answers 503 (overloaded). Without a Groq fallback, only fast transient failures
# (kind "unavailable": 5xx or network errors) are retried, twice, waiting 0.5 s and then 1 s. 4xx
# errors (including 429) and timeouts are never retried. With a fallback, Gemini gets one attempt
# and Groq, an independent provider, takes the place of these retries. This is the only retry
# layer: the SDK's own retries stay off.
GEMINI_RETRY_DELAYS = (0.5, 1.0)

# Gemini failures after which Groq is tried: Gemini could not answer, for reasons that say nothing
# about the question. Not request_failed (another 4xx: the same request would fail again) and never
# anything after SQL generation (validator rejections and database errors are final).
FALLBACK_KINDS = frozenset({"rate_limited", "unavailable", "timeout", "model_unavailable", "not_configured",
                            "invalid_response", "empty_response"})

# Cooperative time budget for one request, below the frontend's 60 s abort. Gemini-only (no Groq
# key) keeps the original 45 s; with the Groq fallback available a request may take up to 50 s.
REQUEST_BUDGET_SECONDS = 45.0
FALLBACK_REQUEST_BUDGET_SECONDS = 50.0
SQL_ATTEMPT_SECONDS = SQL_GENERATION_TIMEOUT_MS / 1000
GROQ_ATTEMPT_SECONDS = GROQ_SQL_TIMEOUT_MS / 1000
INSIGHT_SECONDS = INSIGHT_TIMEOUT_MS / 1000
# Planning reserve, not a measured maximum: when deciding whether another SQL attempt may start,
# keep this much of the budget for the database stage that follows it. The stage's real controls
# are the 5 s connect timeout per connection attempt and the 5 s statement timeout; how long
# connecting can take overall depends on how many addresses the host resolves to, which can change.
# Gemini retries (Gemini-only) keep 20 s; the Groq fallback keeps 15 s, to fit inside 50 s.
DB_STAGE_RESERVE_SECONDS = 20.0
FALLBACK_DB_STAGE_RESERVE_SECONDS = 15.0
# Small allowance for in-process work (validation, chart selection, serialisation) and clock jitter.
SAFETY_MARGIN_SECONDS = 1.0
# Groq only starts if its attempt, the database stage and the margin still fit (10 + 15 + 1 = 26 s).
# A Gemini timeout after its full 20 s leaves 30 s of the 50 s, so the fallback still fits.
FALLBACK_NEEDS_SECONDS = GROQ_ATTEMPT_SECONDS + FALLBACK_DB_STAGE_RESERVE_SECONDS + SAFETY_MARGIN_SECONDS

GENERATION_UNAVAILABLE_MESSAGE = "The AI service is unavailable right now. Please try again later."


class QueryResponse(BaseModel):
    question: str
    sql: str  # the validated SQL that was actually run
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    visualization: Visualization  # chosen by fixed rules, never by the LLM
    insight: str | None  # None when there are no rows or the insight could not be generated
    # The stored History snapshot of this answer (app.history_store), or None if it was not stored.
    # Set by the /query endpoint after the pipeline has finished; the pipeline itself never sets it.
    analysis_id: str | None = None


class QueryServiceError(Exception):
    """The pipeline failed. kind says where and why; the message is safe to show to users.

    Kinds: invalid_question, rate_limited, generation_unavailable, generation_timeout, generation_failed,
    unsafe_sql, database_unavailable, query_timeout, query_not_allowed, query_failed, result_too_large.
    """

    def __init__(self, kind: str, message: str, *, retry_after: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after  # seconds, sent to the client as a Retry-After header


def run_business_query(
    question: str,
    *,
    generate: Callable[[str], str] = generate_sql,
    validate: Callable[[str], str] = validate_sql,
    execute: Callable[[str], QueryResult] = execute_query,
    summarize: Callable[[str, list[str], list[list[Any]], bool], str] = generate_insight,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    metrics: QueryMetrics | None = None,
    fallback: Callable[[str], str] | None = None,
) -> QueryResponse:
    """Answer a business question with data. The keyword arguments exist so tests can swap steps.
    fallback=None means "Groq if GROQ_API_KEY is set, otherwise none", decided on every request."""
    metrics = metrics if metrics is not None else QueryMetrics(request_id=new_request_id())
    question = (question or "").strip()
    if not question:
        metrics.fail("request", "invalid_question")
        raise QueryServiceError("invalid_question", "Please enter a question.")
    if fallback is None and settings.groq_api_key is not None:
        fallback = groq_sql.generate_sql
    deadline = clock() + (FALLBACK_REQUEST_BUDGET_SECONDS if fallback is not None else REQUEST_BUDGET_SECONDS)

    with metrics.timed("sql", clock):
        generated_sql = _generate_sql_with_fallback(question, generate, fallback, sleep,
                                                    lambda: deadline - clock(), metrics)

    with metrics.timed("validation", clock):
        try:
            safe_sql = validate(generated_sql)
        except UnsafeSQLError:
            # The validator's message quotes parts of the generated SQL, so it is not logged.
            metrics.fail("validation", "validator_rejected")
            raise QueryServiceError("unsafe_sql", "The generated query was rejected by the safety checks.") from None

    with metrics.timed("db", clock):
        try:
            result = execute(safe_sql)
        except QueryExecutionError as error:
            metrics.fail("db", f"db_{error.kind}")
            raise _execution_error(error.kind) from None

    visualization = select_visualization(result.columns, result.rows)
    metrics.rows, metrics.truncated, metrics.visualization = len(result.rows), result.truncated, visualization.type
    return QueryResponse(
        question=question,
        sql=safe_sql,
        columns=result.columns,
        rows=result.rows,
        row_count=len(result.rows),
        truncated=result.truncated,
        visualization=visualization,
        insight=_optional_insight(question, result, summarize, lambda: deadline - clock(), metrics, clock),
    )


def _optional_insight(question: str, result: QueryResult, summarize: Callable[..., str],
                      time_left: Callable[[], float], metrics: QueryMetrics,
                      clock: Callable[[], float]) -> str | None:
    """The insight, or None. A failure here never turns a successful query into an error."""
    if metrics.sql_provider == "groq":
        # Gemini, the insight provider, has just failed: do not call it again for this request.
        metrics.insight_status = "skipped_fallback"
        return None
    if not result.rows:
        metrics.insight_status = "skipped_empty"  # nothing to summarise, so no Gemini call
        return None
    if time_left() < INSIGHT_SECONDS + SAFETY_MARGIN_SECONDS:
        # Not enough time for the insight call to finish; the result matters more.
        metrics.insight_status = "skipped_budget"
        return None
    with metrics.timed("insight", clock):
        try:
            insight = summarize(question, result.columns, result.rows, result.truncated)
        except InsightError as error:
            metrics.insight_status, metrics.insight_error = "failed", error.kind
            return None
        except Exception as error:  # the insight is optional; never let it break the answer
            metrics.insight_status, metrics.insight_error = "failed", type(error).__name__
            return None
    metrics.insight_status = "success"
    return insight


def _generate_sql_with_fallback(question: str, generate: Callable[[str], str],
                                fallback: Callable[[str], str] | None, sleep: Callable[[float], None],
                                time_left: Callable[[], float], metrics: QueryMetrics) -> str:
    """SQL from Gemini or, if Gemini fails with one of FALLBACK_KINDS, from one Groq attempt.
    The caller validates and runs it exactly as it would Gemini's: nothing here checks the SQL."""
    if fallback is not None:
        metrics.fallback = "not_used"
    try:
        sql = _generate_with_retry(question, generate, sleep, time_left, metrics,
                                   delays=GEMINI_RETRY_DELAYS if fallback is None else ())
    except SQLGenerationError as error:
        gemini_error = error
    else:
        metrics.sql_provider = "gemini"
        return sql

    metrics.limit_type = gemini_error.limit_type
    if fallback is None or gemini_error.kind not in FALLBACK_KINDS:
        metrics.fail("sql", f"gemini_{gemini_error.kind}")
        raise _generation_error(gemini_error) from None
    metrics.fallback_reason = gemini_error.kind
    if time_left() < FALLBACK_NEEDS_SECONDS:
        metrics.fallback = "skipped_budget"
        metrics.fail("sql", f"gemini_{gemini_error.kind}")
        raise _generation_error(gemini_error) from None

    try:
        sql = fallback(question)  # exactly one Groq attempt
    except SQLGenerationError as error:
        # Both providers failed. One provider-neutral answer, whatever each said; never back to Gemini.
        metrics.fallback = "failed"
        metrics.fail("sql", f"groq_{error.kind}")
        raise QueryServiceError("generation_unavailable", GENERATION_UNAVAILABLE_MESSAGE) from None
    metrics.fallback, metrics.sql_provider = "success", "groq"
    return sql


def _generate_with_retry(question: str, generate: Callable[[str], str], sleep: Callable[[float], None],
                        time_left: Callable[[], float], metrics: QueryMetrics, delays: tuple[float, ...]) -> str:
    """Gemini, retried after fast transient failures while the budget allows; raises its last error."""
    for delay in (*delays, None):
        metrics.gemini_sql_attempts += 1
        try:
            return generate(question)
        except SQLGenerationError as error:
            # Retry only if the wait, a full attempt and the database reserve still fit in the budget.
            needed = (delay or 0) + SQL_ATTEMPT_SECONDS + DB_STAGE_RESERVE_SECONDS
            if error.kind == "unavailable" and delay is not None and time_left() >= needed:
                sleep(delay)
                continue
            raise
    raise AssertionError("unreachable")


# User-facing messages for Gemini 429s. All are HTTP 429 with code "rate_limited"; none is retried.
RATE_LIMIT_MESSAGES = {
    "temporary_rate_limit": "The AI service is receiving too many requests. Please try again shortly.",
    "quota_exhausted": "The AI service usage limit has been reached. Please try again later.",
    "rate_limited": "Too many requests to the AI service. Please try again later.",
}


def _generation_error(error: SQLGenerationError) -> QueryServiceError:
    kind = error.kind
    if kind == "invalid_question":
        return QueryServiceError("invalid_question", "Please enter a question.")
    if kind == "rate_limited":
        message = RATE_LIMIT_MESSAGES.get(error.limit_type, RATE_LIMIT_MESSAGES["rate_limited"])
        return QueryServiceError("rate_limited", message, retry_after=error.retry_after)
    if kind == "timeout":
        return QueryServiceError("generation_timeout", "The AI service took too long to respond. Please try again.")
    if kind in ("unavailable", "not_configured", "model_unavailable"):
        return QueryServiceError("generation_unavailable", GENERATION_UNAVAILABLE_MESSAGE)
    # request_failed, empty_response, invalid_response, or anything new
    return QueryServiceError("generation_failed", "The AI service could not answer this question. Please rephrase it.")


def _execution_error(kind: str) -> QueryServiceError:
    if kind == "unavailable":
        return QueryServiceError("database_unavailable", "The database is unavailable right now. Please try again later.")
    if kind == "timeout":
        return QueryServiceError("query_timeout", "The query took too long to run. Try a narrower question.")
    if kind == "permission_denied":
        return QueryServiceError("query_not_allowed", "The query was refused by the database.")
    if kind == "result_too_large":
        return QueryServiceError("result_too_large",
                                 "The query result is too large to return safely. Please ask a more specific question.")
    return QueryServiceError("query_failed", "The generated query could not be run. Please rephrase your question.")
