"""Small ASGI middlewares: request IDs and summary logs, safe 500s, a body-size cap and security headers.

They are plain ASGI classes (not BaseHTTPMiddleware) so exceptions and streaming behave normally.
main.py decides their order; see the comment there.
"""

import logging
import time
import traceback

from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException

from app.request_log import QueryMetrics, elapsed_ms, log_query_summary, new_request_id

logger = logging.getLogger(__name__)

INTERNAL_ERROR_MESSAGE = "Something went wrong. Please try again."
REQUEST_ID_HEADER = "X-Request-ID"

# Sent on every response. The API serves JSON (and the /docs page), never content to embed.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",  # browsers must not guess another content type
    "X-Frame-Options": "DENY",  # no page may show the API in a frame (clickjacking)
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",  # answers contain business data; do not keep copies
}


def error_response(status_code: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    """The one error shape the API returns: {"error": {"code": ..., "message": ...}}."""
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}}, headers=headers)


class RequestContext:
    """Give every request a server-generated ID, returned as X-Request-ID, and log a summary of
    each POST /query once it has finished.

    The QueryMetrics object is kept in scope["state"], so the endpoint and the error handlers reach
    it as request.state.query_metrics. This sits outside CatchUnexpectedErrors, so safe 500s also
    carry the ID and are logged.
    """

    def __init__(self, app, path: str = "/query", clock=time.monotonic):
        self.app = app
        self.path = path
        self.clock = clock

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        metrics = QueryMetrics(request_id=new_request_id())
        scope.setdefault("state", {})["query_metrics"] = metrics
        started = self.clock()

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                metrics.status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = metrics.request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            if scope.get("method") == "POST" and scope.get("path") == self.path:
                metrics.total_ms = elapsed_ms(self.clock() - started)
                log_query_summary(metrics)


class CatchUnexpectedErrors:
    """Turn any unhandled exception into the standard safe 500 response.

    Starlette's own catch-all runs outside every other middleware, so its 500s carry no CORS
    headers and the browser hides them from the frontend. This runs inside CORSMiddleware instead.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as error:
            # The exception message can contain upstream details (connection strings, SDK
            # payloads), so only its type and the code location are logged.
            metrics = scope.get("state", {}).get("query_metrics")
            if metrics is not None:
                metrics.error_kind = "internal_error"
                metrics.fail(metrics.stage or "internal", type(error).__name__)
            logger.error(
                "Unexpected %s while handling %s %s (request_id=%s)\n%s",
                type(error).__name__, scope.get("method"), scope.get("path"),
                metrics.request_id if metrics else "-",
                "".join(traceback.format_tb(error.__traceback__)),
            )
            if response_started:  # too late to replace the response
                raise
            await error_response(500, "internal_error", INTERNAL_ERROR_MESSAGE)(scope, receive, send)


class RequestBodyTooLarge(HTTPException):
    def __init__(self):
        super().__init__(status_code=413)


class LimitRequestBody:
    """Reject request bodies over max_bytes before they are read into memory.

    The question is at most 500 characters, so a much larger body is never legitimate.
    """

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers") or []).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await error_response(413, "request_too_large", "The request is too large.")(scope, receive, send)
            return

        # Bodies without a Content-Length (chunked) are counted as they arrive.
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise RequestBodyTooLarge()  # handled by main.py's HTTPException handler
            return message

        await self.app(scope, limited_receive, send)


class SecurityHeaders:
    """Add SECURITY_HEADERS to every HTTP response, including errors."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
