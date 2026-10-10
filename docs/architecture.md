# DataPilot Architecture

How DataPilot is built today: what runs where, how a question becomes an answer, and why the
design is kept deliberately small. File and function names refer to this repository.

## 1. System Overview

A user types a business question in plain English. The React frontend sends it to the FastAPI
backend, which asks Gemini to write one PostgreSQL `SELECT`. That SQL is checked by a SQLGlot
validator and, only if approved, run against PostgreSQL through a read-only database role. The
backend then picks a display (KPI, bar chart, line chart or table) with fixed rules, optionally asks
Gemini for a one- or two-sentence insight about the returned rows, and sends everything back. The
frontend shows the insight, the visualization, the result table and the SQL that ran.

The model writes SQL and prose; it never touches the database directly and never decides what is
safe to run.

## 2. Architecture Diagram

```mermaid
flowchart TD
    user(["User (browser)"])

    subgraph frontend["Frontend on Vercel"]
        ui["React + Vite app<br/>question workspace"]
    end

    subgraph backend["Backend: FastAPI on Render"]
        guard["Middleware<br/>body-size cap · CORS<br/>security headers · safe 500s"]
        request["Request validation<br/>1-500 character question"]
        limiter["Rate limiter<br/>per client IP"]
        nl["NL-to-SQL<br/>nl_to_sql.generate_sql"]
        validator["SQLGlot validator<br/>sql_validator.validate_sql"]
        executor["Read-only executor<br/>db_executor.execute_query"]
        selector["Visualization selector<br/>chart_selector"]
        insight["Optional insight<br/>insight_service"]
        response["JSON answer<br/>sql · rows · visualization · insight"]
        rejected["Safe error JSON<br/>SQL never executed"]
    end

    gemini[["Gemini API"]]
    db[("Supabase PostgreSQL<br/>read-only role")]

    subgraph answer["Frontend on Vercel"]
        views["AI insight · KPI / bar / line / table<br/>result table · generated SQL"]
    end

    user --> ui
    ui -- "HTTPS POST /query" --> guard
    guard --> request --> limiter --> nl
    nl -. "SQL prompt" .-> gemini
    nl --> validator
    validator -- "approved" --> executor
    validator -- "rejected" --> rejected
    executor -- "SELECT, read-only" --> db
    executor --> selector --> insight
    insight -. "insight prompt" .-> gemini
    insight --> response
    response --> views
    rejected --> views

    classDef external fill:#fff4e5,stroke:#d97706,color:#111827
    class gemini,db external
    style frontend fill:#f5f7fb,stroke:#94a3b8
    style backend fill:#f5f7fb,stroke:#94a3b8
    style answer fill:#f5f7fb,stroke:#94a3b8
```

Gemini and Supabase (orange) are external services; Vercel hosts the frontend, which appears twice
(sending the question, rendering the answer), and Render hosts the backend. Dotted arrows are calls
to Gemini. Every failure path (not only rejected SQL) ends in the same safe
error JSON, which the frontend shows as an error panel.

## 3. Request Lifecycle

1. **Question.** The user types a question (or picks an example) and presses Analyze or Enter.
   The frontend rejects an empty question itself.
2. **Request.** `frontend/src/services/api.js` sends `POST /query` with `{"question": "..."}` to
   the URL in `VITE_API_BASE_URL`, with a 60-second timeout.
3. **Checks before any AI call.** Middleware rejects bodies over 16 KB and only answers browser
   requests from the configured origins. `QueryRequest` accepts exactly one field, `question`,
   of 1–500 characters. The rate limiter allows 5 questions per 60 seconds per client (configurable).
4. **SQL generation.** `generate_sql` sends Gemini a system prompt containing the six-table schema,
   the business definitions and the SQL rules, with the user's question wrapped as untrusted data.
   Gemini must return structured JSON (`{"sql": ...}`). Transient failures (5xx, network) are
   retried twice, after 0.5 s and 1 s; other errors are not retried.
5. **Validation.** `validate_sql` parses the SQL with SQLGlot and checks the syntax tree: one
   statement, read-only (`SELECT` / `WITH … SELECT`, nothing that writes or locks, including inside
   CTEs), only the six tables, no blocked functions, and a `LIMIT` (added or capped at 501: 500 rows
   plus one row that only shows whether more exist).
6. **Only approved SQL continues.** If validation fails, the request ends with HTTP 400
   `unsafe_sql`; the SQL is never sent to the database. The validator's rewritten SQL (for example
   with the added `LIMIT`) is what runs and what the user sees.
7. **Execution.** `execute_query` connects with `READONLY_DATABASE_URL` as the `datapilot_readonly`
   role, inside a read-only transaction that is always rolled back, with a statement timeout.
8. **Rows.** At most 500 rows are kept (`truncated` says if there were more), converted to
   JSON-friendly values.
9. **Visualization.** `select_visualization` chooses KPI, bar, line or table from the shape of the
   result (section 8). No model is involved.
10. **Insight.** If there are rows, `generate_insight` asks Gemini for a short summary of the first
    50 rows, the row count and the truncated flag. It is never retried.
11. **Insight failures are absorbed.** Any insight error sets `insight` to `null`; the query result
    is still returned with HTTP 200.
12. **Display.** The frontend renders the insight (or a quiet "Insight temporarily unavailable"
    note), the KPI or chart, the result table and the generated SQL.

The **query result is the authoritative answer**. The insight only describes those rows and can be
missing without affecting the result.

## 4. Backend Structure

All backend code is in `backend/app/`.

| Module | Responsibility |
|---|---|
| `main.py` | The FastAPI app: `GET /health`, `POST /query`, the `QueryRequest` model, middleware order, exception handlers that map every error to `{"error": {"code", "message"}}`, and the `enforce_rate_limit` dependency |
| `config.py` | `Settings` (pydantic-settings): database URL, Gemini key and model, CORS origins, rate limit, timeouts, row cap. Secrets are `SecretStr`, so they never appear in logs |
| `query_service.py` | The pipeline, `run_business_query`: generate → validate → execute → select visualization → optional insight. Defines `QueryResponse` and turns step failures into `QueryServiceError` kinds |
| `nl_to_sql.py` | The only code that asks Gemini for SQL: schema context, business definitions, SQL rules, untrusted-input rules, `generate_sql`, and `SQLGenerationError` |
| `sql_validator.py` | `validate_sql`: the SQLGlot safety checks and `LIMIT` enforcement. No network or database access, so it is tested on its own |
| `db_executor.py` | `execute_query`: runs approved SQL as the read-only role, with timeout and row cap; `to_json_value` converts PostgreSQL types; `QueryExecutionError` hides connection details |
| `chart_selector.py` | `select_visualization` and the `Visualization` model (`type`, `x_key`, `y_key`) |
| `insight_service.py` | `generate_insight`: the optional summary prompt, a 15-second timeout, output length checks, and `InsightError` |
| `middleware.py` | Plain ASGI middleware: `RequestContext` (request ID and summary log line), `LimitRequestBody` (16 KB), `CatchUnexpectedErrors` (safe 500s that still carry CORS headers), `SecurityHeaders`, and `error_response` |
| `request_log.py` | Logging setup, request IDs and `QueryMetrics`: the outcome, stage timings and Gemini attempt count of one request, logged as a single `key=value` line |
| `client_ip.py` | `client_ip`: the rate-limit identity, from `CF-Connecting-IP` when `TRUST_CF_CONNECTING_IP` is on, otherwise the TCP peer. `X-Forwarded-For` is never used |
| `rate_limiter.py` | `RateLimiter`: an in-memory sliding window per client |

Middleware runs in this order for each request, outermost first: `SecurityHeaders` → CORS →
`RequestContext` → `CatchUnexpectedErrors` → `LimitRequestBody` → the route.

Setup scripts live in `database/` (`schema.sql`, `seed.py`, `readonly_role.sql`,
`create_readonly_role.py`, `apply_schema.py`). They use the admin connection and are never part of
the running API.

## 5. Frontend Structure

The frontend (`frontend/src/`) is a single page with plain React state; no router or state library.

| File | Responsibility |
|---|---|
| `App.jsx` | Page layout and request state (question, loading, result, error); prevents double submits; shows the empty state, loading skeleton, error panel or answer |
| `services/api.js` | The only module that calls the backend: `postQuery`, the timeout, and mapping HTTP errors to friendly titles and messages |
| `components/QuestionForm.jsx` | The question workspace: textarea (Enter submits, Shift+Enter adds a line), Analyze button, example questions |
| `components/InsightCard.jsx` | The AI insight, or the quiet unavailable note |
| `components/Visualization.jsx` | Draws exactly what the backend chose; it never picks a chart or columns itself |
| `components/KpiCard.jsx` | One value with a readable label, for example "Total Revenue ₹21,304,631.99" |
| `components/BarChartView.jsx`, `LineChartView.jsx` | Recharts charts (horizontal bars; a line in date order) |
| `components/ChartCard.jsx`, `ChartTooltip.jsx` | Shared chart frame and tooltip |
| `components/ResultsTable.jsx` | Row count, truncation note and the raw result table |
| `components/SqlViewer.jsx` | The generated SQL with a Copy SQL button |
| `components/ErrorMessage.jsx` | Error panel: amber for temporary problems, red otherwise |
| `utils/format.js` | All display formatting: ₹ for money-like columns, % for percentage columns, labels, dates |

## 6. Database

A fictional Indian e-commerce store with synthetic data generated by `database/seed.py`
(Faker, fixed seed, orders from 2024-10-01 to 2026-09-30):

| Table | Holds | Rows |
|---|---|---|
| `customers` | name, email, city, state, country, signup date | 1,000 |
| `categories` | product categories | 10 |
| `products` | name, category, **current** price and cost, stock | 120 |
| `orders` | customer, order date, status | 5,000 |
| `order_items` | product, quantity, and the unit price and **unit cost at the time of the order** | 9,241 |
| `payments` | order, amount, method, status, date | 5,257 |

```
customers ──< orders ──< order_items >── products >── categories
                 └──< payments
```

**Historical data:** `order_items.unit_price` and `order_items.unit_cost` are captured when the
order is placed. Revenue and profit use them, so historical figures stay correct if a product's
current `products.price` or `products.cost` changes later. Profit never uses `products.cost`.

**Read-only role:** the API connects only as `datapilot_readonly` (`database/readonly_role.sql`):
`SELECT` on the six tables and nothing else, no inherited privileges, a connection limit, and every
session read-only by default with a 5-second statement timeout.

## 7. Business Semantics

These definitions are written into the NL-to-SQL prompt and into the benchmark's trusted SQL:

| Term | Definition |
|---|---|
| Dataset reference date | 2026-09-30, treated as "today" for relative dates such as "last 6 months" |
| Completed order | `orders.status = 'delivered'` |
| Successful payment | `payments.status = 'completed'` |
| Revenue | `SUM(order_items.quantity * order_items.unit_price)` over delivered orders |
| Profit | `SUM(order_items.quantity * (order_items.unit_price - order_items.unit_cost))` over delivered orders |
| Average order value | revenue / number of distinct delivered orders |

Cancelled and returned orders are excluded from revenue, profit and AOV unless the question asks
about them explicitly. Questions about numbers or shares of orders count all orders unless the
question says otherwise.

## 8. Visualization Decision Logic

`chart_selector.select_visualization` looks only at the result's shape:

| Result | Display |
|---|---|
| one row, one numeric value | KPI |
| 2–50 rows, one text column + one numeric column | bar chart |
| 2+ rows, one date/time column + one numeric column | line chart |
| anything else: no rows, several numeric columns, three or more columns, mixed or unclear types | table only |

The rules are deterministic: the same result always gets the same display, it can be unit-tested,
and it costs no extra model call. If anything is unclear the answer is "table", and the table is
always shown below any chart.

## 9. Deployment Architecture

| Part | Where | Notes |
|---|---|---|
| Frontend | Vercel | Static build of the Vite app; its only setting is the public `VITE_API_BASE_URL` |
| Backend | Render | One web service, `uvicorn … --workers 1 --no-proxy-headers` (`render.yaml`) |
| Database | Supabase PostgreSQL | Reached through Supabase's session pooler as the read-only role |
| AI provider | Gemini API | Called only by the backend |

- The browser calls the Render API directly; Vercel serves static files only.
- `CORS_ALLOWED_ORIGINS` allows the production Vercel origin plus the localhost development origins.
- The Gemini key and database URL exist only as Render environment variables. The frontend bundle
  contains no secrets.
- The running API only has `READONLY_DATABASE_URL`. The admin `DATABASE_URL` is used by the setup
  scripts and is not configured on Render.
- Render's proxy sits in front of the app, so the rate limiter identifies clients by Cloudflare's
  `CF-Connecting-IP` header (`TRUST_CF_CONNECTING_IP=true`).

Step-by-step setup is in [`deployment.md`](deployment.md).

## 10. Design Decisions

The guiding idea is simple, reliable and explainable rather than clever:

- **FastAPI**: small, typed (Pydantic request and response models) and gives `/docs` for free.
- **React + Vite**: a standard, fast frontend toolchain for a single interactive page.
- **PostgreSQL**: a real relational database with roles, read-only transactions and statement
  timeouts, which the safety model relies on.
- **Gemini**: turns questions into SQL and writes the short insight, through direct API calls with
  structured JSON output.
- **SQLGlot**: checks the parsed syntax tree, not the SQL text, so tricks such as comments, odd
  casing or a write hidden in a CTE are caught.
- **Deterministic visualization**: predictable, testable and free, instead of asking the model.
- **Read-only database role**: a second, independent guarantee; writes fail at the database even if
  the validator had a bug.
- **No LangChain or agent framework**: one fixed pipeline with direct calls is easier to read,
  test and explain.
- **No vector database**: the schema is six tables and fits directly in the prompt.
- **No queues, caches, workers or extra services**: one web service and one database are enough for
  this workload. The in-memory rate limiter assumes that single process.

## 11. Failure Isolation

Each failure ends in a specific, safe response; none exposes stack traces, credentials or raw
provider errors.

| Failure | What happens |
|---|---|
| Gemini rate-limits SQL generation | HTTP 429 `rate_limited`, never retried; a `Retry-After` header when Gemini sends a usable retry delay, and a more specific message when its quota metadata shows a temporary limit or an exhausted quota |
| Gemini unavailable (5xx or network, after two retries) | HTTP 503 `generation_unavailable` |
| Gemini returns an empty or malformed answer | HTTP 502 `generation_failed` |
| Generated SQL fails validation | HTTP 400 `unsafe_sql`; the SQL is never executed |
| Database refuses the query (permissions) | HTTP 400 `query_not_allowed` |
| Query error (for example an unknown column) | HTTP 422 `query_failed` |
| Query exceeds the statement timeout | HTTP 504 `query_timeout` |
| Database unreachable | HTTP 503 `database_unavailable` |
| Insight generation fails for any reason | HTTP 200 with the full result and `insight: null` |
| Too many questions from one client | HTTP 429 `too_many_requests` with a `Retry-After` header |
| Request body over 16 KB | HTTP 413 `request_too_large` |
| Any unexpected server error | HTTP 500 `internal_error` with a generic message; the exception type and code location stay in the server log |

On the frontend, temporary problems (rate limits, unavailable services, timeouts, an unreachable
backend) are shown as calm amber notices; other errors as red panels. An unavailable insight is
never shown as an error.

### Request logs

Every response carries a random `X-Request-ID` header, and every `POST /query` produces exactly
one summary line on the `app.request_log` logger, at INFO for successes, WARNING for handled errors
and ERROR for unexpected ones:

```
event=query_complete request_id=9b2e41f07ac3d158 outcome=success status=200 question_length=34 total_ms=2481 gemini_sql_attempts=1 sql_ms=1630 validation_ms=4 db_ms=212 rows=5 truncated=false visualization=bar insight_status=success insight_ms=611
event=query_complete request_id=51c7d0e2a94b6f83 outcome=error status=429 error_kind=rate_limited stage=sql cause=gemini_rate_limited limit_type=quota_exhausted retry_after=40 question_length=21 total_ms=388 gemini_sql_attempts=1 sql_ms=377
```

- `error_kind` is the code the client received; `stage` (`request`, `app_rate_limit`, `sql`,
  `validation`, `db`, `internal`) and `cause` say where and why. Causes include `app_rate_limited`
  (DataPilot's own limit) versus `gemini_rate_limited`, `gemini_unavailable`, `gemini_timeout`,
  `gemini_invalid_response`, `validator_rejected`, `db_timeout`, `db_unavailable` and
  `db_query_error`; an unexpected exception gives its type.
- `gemini_sql_attempts` is counted as attempts happen; `sql_ms` includes the waits between retries.
- `insight_status` is `success`, `failed` (with `insight_error`), `skipped_budget` or
  `skipped_empty`.
- Timings are whole milliseconds from a monotonic clock. Fields that do not apply are left out.

The line never contains the question (only its length), the generated SQL, result rows, client
addresses, connection strings, the API key or provider error bodies. Gemini and validator messages
are not logged either, because they can quote the question or the SQL. Library loggers such as
httpx stay at WARNING, so request URLs are not logged.
