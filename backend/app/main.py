"""DataPilot API entry point."""

import logging

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.client_ip import client_ip
from app.config import settings
from app.middleware import CatchUnexpectedErrors, LimitRequestBody, SecurityHeaders, error_response
from app.query_service import QueryResponse, QueryServiceError, run_business_query
from app.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

MAX_QUESTION_LENGTH = 500
# A 500-character question fits in well under 4 KB of JSON; anything far bigger is not a question.
MAX_REQUEST_BODY_BYTES = 16 * 1024

app = FastAPI(title=settings.app_name)

# Middleware, innermost first (each add_middleware wraps everything added before it):
#   LimitRequestBody      reject oversized bodies before they are read
#   CatchUnexpectedErrors unhandled exceptions -> safe 500 JSON, still inside CORS
#   CORSMiddleware        only the configured frontend origins may call the API; no cookies
#   SecurityHeaders       added to every response, errors included
app.add_middleware(LimitRequestBody, max_bytes=MAX_REQUEST_BODY_BYTES)
app.add_middleware(CatchUnexpectedErrors)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.add_middleware(SecurityHeaders)

rate_limiter = RateLimiter(settings.rate_limit_requests, settings.rate_limit_window_seconds)

# HTTP status for each QueryServiceError kind. Anything unknown becomes a 500.
STATUS_BY_ERROR_KIND = {
    "invalid_question": 400,
    "unsafe_sql": 400,
    "query_not_allowed": 400,
    "query_failed": 422,
    "rate_limited": 429,
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


@app.exception_handler(QueryServiceError)
def handle_query_error(request: Request, error: QueryServiceError) -> JSONResponse:
    return error_response(STATUS_BY_ERROR_KIND.get(error.kind, 500), error.kind, str(error))


@app.exception_handler(RequestValidationError)
def handle_invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
    message = f"Send JSON like {{\"question\": \"...\"}} with a question of 1-{MAX_QUESTION_LENGTH} characters."
    return error_response(400, "invalid_request", message)


@app.exception_handler(StarletteHTTPException)
def handle_http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
    # Replaces FastAPI's {"detail": ...} so every error has the same shape. Headers such as
    # Allow (405) and Retry-After (429) are kept.
    code, message = HTTP_ERRORS.get(error.status_code, ("http_error", "The request could not be completed."))
    return error_response(error.status_code, code, message, headers=error.headers)


def enforce_rate_limit(request: Request) -> None:
    retry_after = rate_limiter.check(client_ip(request))
    if retry_after is not None:
        logger.warning("Rate limit reached for a client; retry in %d s", retry_after)
        raise HTTPException(status_code=429, headers={"Retry-After": str(retry_after)})


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the API is running. Not rate limited."""
    return {"status": "ok", "service": settings.app_name}


@app.post("/query", response_model=QueryResponse, dependencies=[Depends(enforce_rate_limit)])
def query(request: QueryRequest) -> QueryResponse:
    """Answer a business question: generate SQL, validate it, run it read-only, return the rows."""
    return run_business_query(request.question)
