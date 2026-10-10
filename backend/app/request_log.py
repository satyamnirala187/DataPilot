"""Request observability: a request ID, stage timings and one summary log line per POST /query.

app.middleware.RequestContext creates a QueryMetrics for each request; the endpoint, the error
handlers and the query pipeline fill it in, and the middleware logs it once the response is sent:

  event=query_complete request_id=3f9c0a1b7d2e4c58 outcome=success status=200 question_length=26
  total_ms=842 gemini_sql_attempts=1 sql_ms=421 validation_ms=3 db_ms=91 rows=5 truncated=false
  visualization=bar insight_status=success insight_ms=177

Only fixed vocabularies, counts and timings are logged. The question text, the generated SQL,
result rows, connection details, API keys and provider error bodies never are.
"""

import logging
import re
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(levelname)s [%(name)s] %(message)s"  # Render adds its own timestamps
_UNSAFE_CHARACTERS = re.compile(r"[^A-Za-z0-9_.-]")


def configure_logging() -> None:
    """Send the app's own INFO logs to stderr, where Render collects them.

    Uvicorn only configures its own loggers, so without this the app's loggers fall back to
    Python's WARNING default. Library loggers (httpx, google_genai) are left at WARNING: httpx logs
    every request URL at INFO.
    """
    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.INFO)
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        app_logger.addHandler(handler)


def new_request_id() -> str:
    """A random ID for one request. Never derived from anything the client sent."""
    return secrets.token_hex(8)


@dataclass
class QueryMetrics:
    """What one request did. Fields left as None are not logged; the order here is the log order."""

    request_id: str
    status: int | None = None
    error_kind: str | None = None  # the public error code the client received
    stage: str | None = None  # where it failed: request, app_rate_limit, sql, validation, db or internal
    cause: str | None = None  # why, e.g. app_rate_limited, gemini_timeout, validator_rejected, db_timeout
    limit_type: str | None = None  # Gemini 429 only: temporary_rate_limit, quota_exhausted or rate_limited
    retry_after: int | None = None
    question_length: int | None = None
    total_ms: int | None = None
    gemini_sql_attempts: int = 0
    sql_ms: int | None = None  # all SQL-generation attempts, including waits between retries
    validation_ms: int | None = None
    db_ms: int | None = None
    rows: int | None = None
    truncated: bool | None = None
    visualization: str | None = None
    insight_status: str | None = None  # success, failed, skipped_budget or skipped_empty
    insight_error: str | None = None  # the insight error kind, or the exception type if unexpected
    insight_ms: int | None = None

    def fail(self, stage: str, cause: str) -> None:
        self.stage, self.cause = stage, cause

    @contextmanager
    def timed(self, stage: str, clock: Callable[[], float]) -> Iterator[None]:
        """Record the stage's duration as <stage>_ms, in whole milliseconds, even if it raises.
        An exception leaving the block marks this as the failed stage unless one is already set."""
        start = clock()
        try:
            yield
        except Exception:
            self.stage = self.stage or stage
            raise
        finally:
            setattr(self, f"{stage}_ms", elapsed_ms(clock() - start))

    @property
    def outcome(self) -> str:
        failed = self.error_kind is not None or self.status is None or self.status >= 400
        return "error" if failed else "success"

    def summary(self) -> str:
        parts = ["event=query_complete", f"request_id={self.request_id}", f"outcome={self.outcome}"]
        for field in fields(self)[1:]:
            value = getattr(self, field.name)
            if value is not None:
                parts.append(f"{field.name}={_format(value)}")
        return " ".join(parts)


def log_query_summary(metrics: QueryMetrics) -> None:
    if metrics.error_kind == "internal_error" or metrics.status is None:
        level = logging.ERROR
    elif metrics.outcome == "error":
        level = logging.WARNING
    else:
        level = logging.INFO
    logger.log(level, "%s", metrics.summary())


# POST /auth/login outcomes, from the response status alone: the log never says which credential
# was wrong and never includes the username, password or token.
LOGIN_OUTCOMES = {200: "success", 400: "invalid_request", 401: "failure", 429: "rate_limited", 503: "unavailable"}


def log_login(metrics: QueryMetrics) -> None:
    outcome = LOGIN_OUTCOMES.get(metrics.status, "error")
    level = logging.INFO if outcome == "success" else logging.WARNING
    logger.log(level, "event=login request_id=%s outcome=%s status=%s total_ms=%s",
               metrics.request_id, outcome, metrics.status, metrics.total_ms)


def elapsed_ms(seconds: float) -> int:
    return max(0, round(seconds * 1000))


def _format(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    # Every value comes from a fixed vocabulary; this only guarantees one token per field.
    return _UNSAFE_CHARACTERS.sub("_", str(value))[:64]
