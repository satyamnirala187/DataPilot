"""Turn a natural-language business question into one PostgreSQL SELECT, using GroqCloud.

The planned fallback for app.nl_to_sql (Gemini); nothing calls it yet. It sends exactly the same
instructions as Gemini (SYSTEM_INSTRUCTION and build_user_prompt from app.nl_to_sql), returns SQL
text only and raises the same SQLGenerationError kinds, so a caller can treat both providers alike.

Like app.nl_to_sql, this module only generates SQL. It never connects to the database or runs
anything, and it is not a security boundary: everything it returns must still pass
app.sql_validator before it reaches the executor.
"""

import math
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import settings
# Shared with Gemini so both providers get identical instructions and parse answers the same way.
from app.nl_to_sql import (MAX_RETRY_AFTER_SECONDS, SYSTEM_INSTRUCTION, TIMEOUT_ERRORS, GeneratedSQL,
                           SQLGenerationError, _strip_code_fences, build_user_prompt)

# Groq's OpenAI-compatible Chat Completions endpoint, called directly with httpx (no Groq SDK).
GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
# Longest one Groq SQL call may take; httpx applies it to connecting and to each read. Shorter than
# Gemini's 20 s because, as the fallback, it will run after Gemini has used part of the request
# budget. httpx never retries a request by itself, so each call is exactly one attempt.
GROQ_SQL_TIMEOUT_MS = 15_000
# openai/gpt-oss-120b is a reasoning model: its reasoning tokens count towards this limit, so it
# leaves room for low-effort reasoning plus a long query. A response cut off by it is rejected.
MAX_COMPLETION_TOKENS = 2048
# The structured response, as a JSON schema: {"sql": "..."} only, the same shape as GeneratedSQL.
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "generated_sql",
        "schema": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"],
                   "additionalProperties": False},
    },
}


def generate_sql(question: str, *, client: httpx.Client | None = None) -> str:
    """Ask Groq for one PostgreSQL SELECT answering the question. Not validated or executed."""
    if not question or not question.strip():
        raise SQLGenerationError("invalid_question", "The question is empty.")
    payload = {
        "model": settings.groq_model,
        "messages": [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": build_user_prompt(question)},
        ],
        "response_format": RESPONSE_FORMAT,
        "temperature": 0,
        "reasoning_effort": "low",  # SQL needs little reasoning; keeps latency and token use down
        "max_completion_tokens": MAX_COMPLETION_TOKENS,
    }

    own_client = client is None
    client = client or _default_client()
    try:
        response = client.post(GROQ_CHAT_COMPLETIONS_URL, json=payload)
    except TIMEOUT_ERRORS:
        raise SQLGenerationError("timeout", f"Groq did not respond within {GROQ_SQL_TIMEOUT_MS // 1000} s.") from None
    except Exception:  # network failures, unexpected client errors
        raise SQLGenerationError("unavailable", "Could not reach the Groq API.") from None
    finally:
        if own_client:
            client.close()

    if not response.is_success:
        raise _http_error(response)
    return _extract_sql(response)


def _default_client() -> httpx.Client:
    if settings.groq_api_key is None:
        raise SQLGenerationError("not_configured", "GROQ_API_KEY is not configured.")
    return httpx.Client(
        timeout=GROQ_SQL_TIMEOUT_MS / 1000,
        headers={"Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}"},
    )


def _http_error(response: httpx.Response) -> SQLGenerationError:
    # Only the status code, the Retry-After header and Groq's fixed error code are used: the error
    # message in the body can echo request details.
    code = response.status_code
    if code == 429:
        return SQLGenerationError("rate_limited", "Groq rate limit reached.", limit_type="rate_limited",
                                  retry_after=_retry_after_seconds(response.headers.get("retry-after")))
    if code in (401, 403):
        return SQLGenerationError("not_configured", "Groq rejected the API key.")
    if code == 404:
        return SQLGenerationError("model_unavailable", f"Groq model '{settings.groq_model}' is not available.")
    if code >= 500:
        return SQLGenerationError("unavailable", f"Groq service error (HTTP {code}).")
    if code == 400 and _error_code(response) == "json_validate_failed":
        # The model's output did not match RESPONSE_FORMAT: a bad answer, not a bad request.
        return SQLGenerationError("invalid_response", "Groq returned a response in an unexpected format.")
    return SQLGenerationError("request_failed", f"Groq rejected the request (HTTP {code}).")


def _error_code(response: httpx.Response) -> str | None:
    """Groq's machine-readable error code ({"error": {"code": ...}}), or None."""
    try:
        code = response.json()["error"]["code"]
    except (ValueError, LookupError, TypeError):
        return None
    return code if isinstance(code, str) else None


def _retry_after_seconds(value: str | None) -> int | None:
    """A Retry-After header in seconds ("7", "1.5") as whole seconds, rounded up, or None.
    Same bounds as Gemini's RetryInfo: longer delays are capped; more than a day is invalid."""
    try:
        seconds = float(value) if value is not None else None
    except ValueError:  # e.g. the HTTP-date form, which Groq does not use
        return None
    if seconds is None or not math.isfinite(seconds) or seconds < 0 or seconds > 86_400:
        return None
    return min(MAX_RETRY_AFTER_SECONDS, max(1, math.ceil(seconds)))


def _extract_sql(response: httpx.Response) -> str:
    try:
        choice = response.json()["choices"][0]
        content: Any = choice["message"].get("content")
        finish_reason = choice.get("finish_reason")
    except (ValueError, LookupError, TypeError, AttributeError):
        raise SQLGenerationError("invalid_response", "Groq returned a response in an unexpected format.") from None

    if finish_reason == "length":
        raise SQLGenerationError("invalid_response", "Groq's response was cut off.")
    if content is None or (isinstance(content, str) and not content.strip()):
        raise SQLGenerationError("empty_response", "Groq returned an empty response.")
    try:
        parsed = GeneratedSQL.model_validate_json(content)
    except (ValidationError, ValueError, TypeError):
        raise SQLGenerationError("invalid_response", "Groq returned a response in an unexpected format.") from None

    sql = _strip_code_fences(parsed.sql).strip()
    if not sql:
        raise SQLGenerationError("empty_response", "Groq returned an empty query.")
    return sql
