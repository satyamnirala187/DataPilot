"""DataPilot API entry point."""

import time
import unicodedata
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import auth
from app.client_ip import client_ip
from app.config import settings
from app.history_store import (AlreadySaved, AnalysisDetail, AnalysisSummary, HistoryUnavailable, SavedReport,
                               SavedReportDetail, SavedReportSummary, get_analysis, get_saved_report, list_history,
                               list_saved_reports, record_analysis, save_report)
from app.middleware import CatchUnexpectedErrors, LimitRequestBody, RequestContext, SecurityHeaders, error_response
from app.query_service import QueryResponse, QueryServiceError, run_business_query
from app.rate_limiter import DailyLimit, RateLimiter
from app.request_log import QueryMetrics, configure_logging, log_history_unavailable

configure_logging()

MAX_QUESTION_LENGTH = 500
# A 500-character question fits in well under 4 KB of JSON; anything far bigger is not a question.
MAX_REQUEST_BODY_BYTES = 16 * 1024

API_DOCS_URLS = {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


def api_docs_urls(enabled: bool) -> dict[str, str | None]:
    """FastAPI's docs routes, or None for each when they are switched off (they then return 404)."""
    return API_DOCS_URLS if enabled else dict.fromkeys(API_DOCS_URLS)


app = FastAPI(title=settings.app_name, **api_docs_urls(settings.enable_api_docs))

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
    allow_headers=["Content-Type", "Authorization"],  # Authorization carries the demo session token
)
app.add_middleware(SecurityHeaders)

# Two separate limits on POST /query, checked before anything calls Gemini:
#   1. rate_limiter (enforce_rate_limit dependency): per client, against bursts; runs first.
#   2. daily_limit (in the endpoint): all clients together, per Pacific day; a safety brake on AI usage.
#      Only well-formed requests that passed the first limit reach it, and each one it admits uses
#      one unit, whatever happens next in the pipeline.
rate_limiter = RateLimiter(settings.rate_limit_requests, settings.rate_limit_window_seconds)
# Login attempts, per client, separate from the /query limit (app/auth.py).
login_limiter = RateLimiter(auth.LOGIN_ATTEMPTS, auth.LOGIN_WINDOW_SECONDS)
daily_limit = DailyLimit(settings.global_daily_query_limit)
DAILY_LIMIT_MESSAGE = "The service has reached its daily AI request limit. Please try again later."
# The History and Saved Reports endpoints, per client, separate from both /query limits: they never
# call Gemini, but each request opens a database connection.
APP_DATA_REQUESTS_PER_MINUTE = 60
app_data_limiter = RateLimiter(APP_DATA_REQUESTS_PER_MINUTE, 60)

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
    401: ("unauthorized", "Please log in to use DataPilot."),
    404: ("not_found", "This endpoint does not exist."),
    405: ("method_not_allowed", "This endpoint does not accept that HTTP method."),
    413: ("request_too_large", "The request is too large."),
    429: ("too_many_requests", "You are asking questions too quickly. Please wait a minute and try again."),
}


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class LoginResponse(BaseModel):
    token: str
    expires_at: str  # ISO 8601, UTC


class SessionResponse(BaseModel):
    authenticated: bool
    expires_at: str


class LoginRejected(Exception):
    """A login was refused. The same 401 is used whether the username or the password was wrong."""

    RESPONSES = {
        "invalid_credentials": (401, "Incorrect username or password."),
        "too_many_login_attempts": (429, "Too many login attempts. Please wait a few minutes and try again."),
        "login_unavailable": (503, "Login is not available right now. Please try again later."),
    }

    def __init__(self, code: str, retry_after: int | None = None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class AppDataError(Exception):
    """A History or Saved Reports request that cannot be answered. An id that does not exist and an id
    that belongs to another account get the same 404, so the response never says which."""

    RESPONSES = {
        "analysis_not_found": (404, "This analysis was not found."),
        "report_not_found": (404, "This saved report was not found."),
        "already_saved": (409, "This analysis is already saved as a report."),
        "too_many_requests": (429, "Too many requests. Please wait a moment and try again."),
    }

    def __init__(self, code: str, retry_after: int | None = None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


HISTORY_UNAVAILABLE_MESSAGE = "History is not available right now. Please try again later."
MAX_TITLE_LENGTH = 120
DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT = 50, 100
ListLimit = Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)]


class SaveReportRequest(BaseModel):
    """Only an id and a title: the report's content is the stored analysis, never data from the client."""

    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID
    title: str

    @field_validator("title")
    @classmethod
    def clean_title(cls, title: str) -> str:
        title = title.strip()
        if not 1 <= len(title) <= MAX_TITLE_LENGTH:
            raise ValueError(f"title must be 1-{MAX_TITLE_LENGTH} characters")
        if any(unicodedata.category(char) == "Cc" for char in title):
            raise ValueError("title must not contain control characters")
        return title


class HistoryList(BaseModel):
    items: list[AnalysisSummary]


class SavedReportList(BaseModel):
    items: list[SavedReportSummary]


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


@app.exception_handler(LoginRejected)
def handle_login_rejected(request: Request, error: LoginRejected) -> JSONResponse:
    status, message = LoginRejected.RESPONSES[error.code]
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
    return error_response(status, error.code, message, headers=headers)


@app.exception_handler(AppDataError)
def handle_app_data_error(request: Request, error: AppDataError) -> JSONResponse:
    status, message = AppDataError.RESPONSES[error.code]
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
    return error_response(status, error.code, message, headers=headers)


@app.exception_handler(HistoryUnavailable)
def handle_history_unavailable(request: Request, error: HistoryUnavailable) -> JSONResponse:
    # The kind is a fixed word or an exception class name: never a URL, SQL or a database message.
    log_history_unavailable(query_metrics(request).request_id, request.scope["route"].name, error.kind)
    return error_response(503, "history_unavailable", HISTORY_UNAVAILABLE_MESSAGE)


INVALID_QUERY_MESSAGE = f"Send JSON like {{\"question\": \"...\"}} with a question of 1-{MAX_QUESTION_LENGTH} characters."
INVALID_REQUEST_MESSAGE = (f"The request is not valid. Check the id, the limit (1-{MAX_LIST_LIMIT}) "
                           f"or the title (1-{MAX_TITLE_LENGTH} characters).")


@app.exception_handler(RequestValidationError)
def handle_invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
    metrics = query_metrics(request)
    metrics.error_kind = "invalid_request"
    metrics.fail("request", "invalid_request")
    message = INVALID_QUERY_MESSAGE if request.url.path == "/query" else INVALID_REQUEST_MESSAGE
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


def enforce_app_data_limit(request: Request) -> None:
    retry_after = app_data_limiter.check(client_ip(request))
    if retry_after is not None:
        raise AppDataError("too_many_requests", retry_after=retry_after)


def enforce_login_limit(request: Request) -> None:
    retry_after = login_limiter.check(client_ip(request))
    if retry_after is not None:
        raise LoginRejected("too_many_login_attempts", retry_after=retry_after)


def iso_utc(unix_seconds: int) -> str:
    return datetime.fromtimestamp(unix_seconds, UTC).isoformat().replace("+00:00", "Z")


@app.post("/auth/login", response_model=LoginResponse, dependencies=[Depends(enforce_login_limit)])
def login(body: LoginRequest) -> LoginResponse:
    """Exchange the shared demo username and password for a session token. Never calls Gemini."""
    if not auth.is_configured():
        raise LoginRejected("login_unavailable")
    if not auth.credentials_match(body.username, body.password):
        raise LoginRejected("invalid_credentials")
    token, session = auth.issue_token()
    return LoginResponse(token=token, expires_at=iso_utc(session.expires_at))


@app.get("/auth/session", response_model=SessionResponse)
def session(current: auth.Session = Depends(auth.require_session)) -> SessionResponse:
    """Is this token still valid? 401 if not."""
    return SessionResponse(authenticated=True, expires_at=iso_utc(current.expires_at))


@app.post("/auth/logout", status_code=204)
def logout(request: Request) -> Response:
    """End this session. Always 204, so the response never says whether the token was valid."""
    try:
        auth.revoked.revoke(auth.session_from_header(request.headers.get("authorization")))
    except auth.AuthError:
        pass
    return Response(status_code=204)


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the API is running. Not rate limited."""
    return {"status": "ok", "service": settings.app_name}


# Signed-in check first: a request without a valid session uses no rate-limit slot, no daily-cap
# unit and never reaches Gemini or the database. (The `session` parameter reuses that same check's
# result: FastAPI runs a dependency once per request.)
@app.post("/query", response_model=QueryResponse,
          dependencies=[Depends(auth.require_session), Depends(enforce_rate_limit)])
def query(body: QueryRequest, request: Request, session: auth.Session = Depends(auth.require_session)) -> QueryResponse:
    """Answer a business question: generate SQL, validate it, run it read-only, return the rows.
    The answer is then stored as History; if that fails the answer is still returned, without an id."""
    metrics = query_metrics(request)
    metrics.question_length = len(body.question)  # the length only; the text is never logged
    enforce_daily_limit(metrics)
    response = run_business_query(body.question, metrics=metrics)  # any failure raises: nothing is stored
    return response.model_copy(update={"analysis_id": store_history(response, session, metrics)})


def store_history(response: QueryResponse, session: auth.Session, metrics: QueryMetrics) -> str | None:
    """Store a successful answer for the session's account (never its session id); the new
    analysis id, or None. record_analysis never raises, so this cannot turn the answer into an error."""
    with metrics.timed("history", time.monotonic):
        result = record_analysis(response, account_id=session.account_id)
    metrics.history_status, metrics.history_error = result.status, result.error
    return result.analysis_id


def enforce_daily_limit(metrics: QueryMetrics) -> None:
    retry_after = daily_limit.acquire()
    if retry_after is not None:
        # Logged as cause=global_daily_limit; the configured limit is never shown or logged.
        metrics.fail("app_rate_limit", "global_daily_limit")
        raise QueryServiceError("daily_limit_reached", DAILY_LIMIT_MESSAGE, retry_after=retry_after)


# --- History and Saved Reports -------------------------------------------------------------------
# Read stored answers and save them as reports. Everything comes from datapilot.analyses and
# datapilot.saved_reports (app/history_store.py): nothing here calls Gemini, regenerates SQL, runs a
# stored query or uses the daily cap. Data belongs to the session's account, never to its session
# id, so it survives logout. Signed-in check first, then the per-client limit: a request without a
# valid session uses no slot. If the History database cannot be used: 503 history_unavailable.
APP_DATA = [Depends(auth.require_session), Depends(enforce_app_data_limit)]
Signed = Annotated[auth.Session, Depends(auth.require_session)]  # the same check's result, run once


@app.get("/history", response_model=HistoryList, dependencies=APP_DATA)
def history(session: Signed, limit: ListLimit = DEFAULT_LIST_LIMIT) -> HistoryList:
    """The account's stored analyses, newest first: summaries without rows or SQL."""
    return HistoryList(items=list_history(session.account_id, limit))


@app.get("/history/{analysis_id}", response_model=AnalysisDetail, dependencies=APP_DATA)
def history_detail(analysis_id: UUID, session: Signed) -> AnalysisDetail:
    """One stored analysis, exactly as it was answered, with its saved report if any."""
    analysis = get_analysis(session.account_id, analysis_id)
    if analysis is None:
        raise AppDataError("analysis_not_found")
    return analysis


@app.post("/saved-reports", response_model=SavedReport, status_code=201, dependencies=APP_DATA)
def create_saved_report(body: SaveReportRequest, session: Signed) -> SavedReport:
    """Save one of the account's analyses under a title. One report per analysis."""
    try:
        report = save_report(session.account_id, body.analysis_id, body.title)
    except AlreadySaved:
        raise AppDataError("already_saved") from None
    if report is None:
        raise AppDataError("analysis_not_found")
    return report


@app.get("/saved-reports", response_model=SavedReportList, dependencies=APP_DATA)
def saved_reports(session: Signed, limit: ListLimit = DEFAULT_LIST_LIMIT) -> SavedReportList:
    """The account's saved reports, most recently saved first: summaries without rows or SQL."""
    return SavedReportList(items=list_saved_reports(session.account_id, limit))


@app.get("/saved-reports/{report_id}", response_model=SavedReportDetail, dependencies=APP_DATA)
def saved_report_detail(report_id: UUID, session: Signed) -> SavedReportDetail:
    """One saved report with its full stored analysis."""
    report = get_saved_report(session.account_id, report_id)
    if report is None:
        raise AppDataError("report_not_found")
    return report
