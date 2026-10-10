"""DataPilot API entry point."""

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.client_ip import client_ip
from app.config import settings
from app.middleware import CatchUnexpectedErrors, LimitRequestBody, RequestContext, SecurityHeaders, error_response
from app.query_service import QueryResponse, QueryServiceError, run_business_query
from app.rate_limiter import DailyLimit, RateLimiter
from app.request_log import QueryMetrics, configure_logging

configure_logging()

MAX_QUESTION_LENGTH = 500
# A 500-character question fits in well under 4 KB of JSON; anything far bigger is not a question.
MAX_REQUEST_BODY_BYTES = 16 * 1024

app = FastAPI(title=settings.app_name)

# Middleware, innermost first (each add_middleware wraps everything added before it):
#   LimitRequestBody      reject oversized bodies before they are read
#   CatchUnexpectedErrors unhandled exceptions -> safe 500 JSON, still inside CORS
#   RequestContext        X-Request-ID on every response; one summary log line per POST /query
#   CORSMiddleware        only the configured frontend origins may call the API; no cookies
#   SecurityHeaders       added to every response, errors included
app.add_middleware(LimitRequestBody, max_bytes=MAX_REQUEST_BODY_BYTES)
app.add_middleware(CatchUnexpectedErrors)
app.add_middleware(RequestContext)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.add_middleware(SecurityHeaders)

# Two separate limits on POST /query, checked before anything calls Gemini:
#   1. rate_limiter (enforce_rate_limit dependency): per client, against bursts; runs first.
#   2. daily_limit (in the endpoint): all clients together, per Pacific day; a safety brake on AI usage.
#      Only well-formed requests that passed the first limit reach it, and each one it admits uses
#      one unit, whatever happens next in the pipeline.
rate_limiter = RateLimiter(settings.rate_limit_requests, settings.rate_limit_window_seconds)
daily_limit = DailyLimit(settings.global_daily_query_limit)
DAILY_LIMIT_MESSAGE = "The service has reached its daily AI request limit. Please try again later."

# HTTP status for each QueryServiceError kind. Anything unknown becomes a 500.
STATUS_BY_ERROR_KIND = {
    "invalid_question": 400,
    "unsafe_sql": 400,
    "query_not_allowed": 400,
    "query_failed": 422,
    "result_too_large": 422,
    "rate_limited": 429,
    "daily_limit_reached": 429,
    "generation_failed": 502,
    "generation_unavailable": 503,
    "generation_timeout": 503,
    "database_unavailable": 503,
    "query_timeout": 504,
}

# Code and message for errors raised by the framework or this module (unknown path, wrong method, ...).
HTTP_ERRORS = {
    404: ("not_found", "This endpoint does not exist."),
    405: ("method_not_allowed", "This endpoint does not accept that HTTP method."),
    413: ("request_too_large", "The request is too large."),
    429: ("too_many_requests", "You are asking questions too quickly. Please wait a minute and try again."),
}


class QueryRequest(BaseModel):
    # Unknown fields are rejected rather than silently ignored.
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)


def query_metrics(request: Request) -> QueryMetrics:
    """This request's metrics, created by RequestContext and logged when the response is sent."""
    return request.state.query_metrics


@app.exception_handler(QueryServiceError)
def handle_query_error(request: Request, error: QueryServiceError) -> JSONResponse:
    metrics = query_metrics(request)
    metrics.error_kind, metrics.retry_after = error.kind, error.retry_after
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
    return error_response(STATUS_BY_ERROR_KIND.get(error.kind, 500), error.kind, str(error), headers=headers)


@app.exception_handler(RequestValidationError)
def handle_invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
    metrics = query_metrics(request)
    metrics.error_kind = "invalid_request"
    metrics.fail("request", "invalid_request")
    message = f"Send JSON like {{\"question\": \"...\"}} with a question of 1-{MAX_QUESTION_LENGTH} characters."
    return error_response(400, "invalid_request", message)


@app.exception_handler(StarletteHTTPException)
def handle_http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
    # Replaces FastAPI's {"detail": ...} so every error has the same shape. Headers such as
    # Allow (405) and Retry-After (429) are kept.
    code, message = HTTP_ERRORS.get(error.status_code, ("http_error", "The request could not be completed."))
    query_metrics(request).error_kind = code
    return error_response(error.status_code, code, message, headers=error.headers)


def enforce_rate_limit(request: Request) -> None:
    retry_after = rate_limiter.check(client_ip(request))
    if retry_after is not None:
        # DataPilot's own limit, logged as cause=app_rate_limited (Gemini's is cause=gemini_rate_limited).
        # The client address is never logged.
        metrics = query_metrics(request)
        metrics.fail("app_rate_limit", "app_rate_limited")
        metrics.retry_after = retry_after
        raise HTTPException(status_code=429, headers={"Retry-After": str(retry_after)})


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the API is running. Not rate limited."""
    return {"status": "ok", "service": settings.app_name}


@app.post("/query", response_model=QueryResponse, dependencies=[Depends(enforce_rate_limit)])
def query(body: QueryRequest, request: Request) -> QueryResponse:
    """Answer a business question: generate SQL, validate it, run it read-only, return the rows."""
    metrics = query_metrics(request)
    metrics.question_length = len(body.question)  # the length only; the text is never logged
    enforce_daily_limit(metrics)
    return run_business_query(body.question, metrics=metrics)


def enforce_daily_limit(metrics: QueryMetrics) -> None:
    retry_after = daily_limit.acquire()
    if retry_after is not None:
        # Logged as cause=global_daily_limit; the configured limit is never shown or logged.
        metrics.fail("app_rate_limit", "global_daily_limit")
        raise QueryServiceError("daily_limit_reached", DAILY_LIMIT_MESSAGE, retry_after=retry_after)
