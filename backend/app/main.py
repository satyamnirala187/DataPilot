"""DataPilot API entry point."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.query_service import QueryResponse, QueryServiceError, run_business_query

logger = logging.getLogger(__name__)

MAX_QUESTION_LENGTH = 500

app = FastAPI(title=settings.app_name)

# Only the configured frontend origins may call the API from a browser. No cookies are used.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# HTTP status for each QueryServiceError kind. Anything unknown becomes a 500.
STATUS_BY_ERROR_KIND = {
    "invalid_question": 400,
    "unsafe_sql": 400,
    "query_not_allowed": 400,
    "query_failed": 422,
    "rate_limited": 429,
    "generation_failed": 502,
    "generation_unavailable": 503,
    "database_unavailable": 503,
    "query_timeout": 504,
}


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


@app.exception_handler(QueryServiceError)
def handle_query_error(request: Request, error: QueryServiceError) -> JSONResponse:
    return error_response(STATUS_BY_ERROR_KIND.get(error.kind, 500), error.kind, str(error))


@app.exception_handler(RequestValidationError)
def handle_invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
    message = f"Send JSON like {{\"question\": \"...\"}} with a question of 1-{MAX_QUESTION_LENGTH} characters."
    return error_response(400, "invalid_request", message)


@app.exception_handler(Exception)
def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    logger.exception("Unexpected error while handling %s %s", request.method, request.url.path)
    return error_response(500, "internal_error", "Something went wrong. Please try again.")


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the API is running."""
    return {"status": "ok", "service": settings.app_name}


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    """Answer a business question: generate SQL, validate it, run it read-only, return the rows."""
    return run_business_query(request.question)
