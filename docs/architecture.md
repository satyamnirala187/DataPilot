# DataPilot Architecture

How DataPilot is built today: what runs where, how a question becomes an answer, and why the
design is kept deliberately small. File and function names refer to this repository.

## 1. System Overview

A user types a business question in plain English. The React frontend sends it to the FastAPI
backend, which asks Gemini to write one PostgreSQL `SELECT`. If Gemini cannot answer and a
GroqCloud key is configured, GroqCloud gets one attempt instead (see
[SQL-generation fallback](#sql-generation-fallback)). That SQL is checked by a SQLGlot
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
        nl["NL-to-SQL<br/>nl_to_sql (Gemini)<br/>groq_sql (fallback)"]
        validator["SQLGlot validator<br/>sql_validator.validate_sql"]
        executor["Read-only executor<br/>db_executor.execute_query"]
        selector["Visualization selector<br/>chart_selector"]
        insight["Optional insight<br/>insight_service"]
        response["JSON answer<br/>sql · rows · visualization · insight"]
        rejected["Safe error JSON<br/>SQL never executed"]
    end

    gemini[["Gemini API"]]
    groq[["GroqCloud API<br/>SQL fallback only"]]
    db[("Supabase PostgreSQL<br/>read-only role")]

    subgraph answer["Frontend on Vercel"]
        views["AI insight · KPI / bar / line / table<br/>result table · generated SQL"]
    end

    user --> ui
    ui -- "HTTPS POST /query" --> guard
    guard --> request --> limiter --> nl
    nl -. "SQL prompt" .-> gemini
    nl -. "same SQL prompt, if Gemini cannot answer" .-> groq
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
    class gemini,groq,db external
    style frontend fill:#f5f7fb,stroke:#94a3b8
    style backend fill:#f5f7fb,stroke:#94a3b8
    style answer fill:#f5f7fb,stroke:#94a3b8
```

Gemini, GroqCloud and Supabase (orange) are external services; Vercel hosts the frontend, which
appears twice (sending the question, rendering the answer), and Render hosts the backend. Dotted
arrows are calls to the AI providers; GroqCloud is called only when Gemini could not write the SQL
and `GROQ_API_KEY` is set. Every failure path (not only rejected SQL) ends in the same safe
error JSON, which the frontend shows as an error panel.

## 3. Request Lifecycle

1. **Login.** The app opens on a login page. `POST /auth/login` checks the shared demo username
   and password on the backend and returns a token valid for 2 hours, kept in `sessionStorage`.
   On reload, `GET /auth/session` confirms it before the workspace is shown.
2. **Question.** The user types a question (or picks an example) and presses Analyze or Enter.
   The frontend rejects an empty question itself.
3. **Request.** `frontend/src/services/api.js` sends `POST /query` with `{"question": "..."}` and
   `Authorization: Bearer <token>` to the URL in `VITE_API_BASE_URL`, with a 60-second timeout.
4. **Checks before any AI call.** The session is checked first: without a valid one the request
   gets 401 and nothing else runs. Middleware rejects bodies over 16 KB and only answers browser
   requests from the configured origins. `QueryRequest` accepts exactly one field, `question`,
   of 1–500 characters. The rate limiter allows 5 questions per 60 seconds per client, and a global
   cap allows a fixed number of questions per Pacific day from all clients together (both configurable).
5. **SQL generation.** `nl_to_sql.generate_sql` sends Gemini a system prompt containing the
   six-table schema, the business definitions and the SQL rules, with the user's question wrapped
   as untrusted data. Gemini must return structured JSON (`{"sql": ...}`). Without `GROQ_API_KEY`,
   transient failures (5xx, network) are retried twice, after 0.5 s and 1 s; other errors are not
   retried. With it, Gemini gets one attempt, and GroqCloud may write the SQL instead (see
   [SQL-generation fallback](#sql-generation-fallback)).
6. **Validation.** `validate_sql` parses the SQL with SQLGlot and checks the syntax tree: one
   statement, read-only (`SELECT` / `WITH … SELECT`, nothing that writes or locks, including inside
   CTEs), only the six tables, no blocked or amplifying functions, no cross joins or recursion, and
   a `LIMIT` (added or capped at 501: 500 rows plus one row that only shows whether more exist).
7. **Only approved SQL continues.** If validation fails, the request ends with HTTP 400
   `unsafe_sql`; the SQL is never sent to the database. The validator's rewritten SQL (for example
   with the added `LIMIT`) is what runs and what the user sees. A rejection is final: the question
   is never sent to another model.
8. **Execution.** `execute_query` connects with `READONLY_DATABASE_URL` as the `datapilot_readonly`
   role, inside a read-only transaction that is always rolled back, with a statement timeout.
9. **Rows.** At most 500 rows are kept (`truncated` says if there were more), converted to
   JSON-friendly values. A result whose JSON exceeds `MAX_RESULT_BYTES` (default 1 MB) is refused.
10. **Visualization.** `select_visualization` chooses KPI, bar, line or table from the shape of the
   result (section 8). No model is involved.
11. **Insight.** If there are rows, `generate_insight` asks Gemini for a short summary of the first
    50 rows, the row count and the truncated flag. It is never retried. When GroqCloud wrote the
    SQL, the insight is skipped: Gemini has just failed, so it is not called again.
12. **Insight failures are absorbed.** Any insight error sets `insight` to `null`; the query result
    is still returned with HTTP 200.
13. **Display.** The frontend renders the insight (or a quiet "Insight temporarily unavailable"
    note), the KPI or chart, the result table and the generated SQL.

The **query result is the authoritative answer**. The insight only describes those rows and can be
missing without affecting the result.

### SQL-generation fallback

One primary provider and one controlled fallback, for SQL generation only:

| | Provider | Model | Used for |
|---|---|---|---|
| Primary | Gemini | `gemini-3.6-flash` | SQL and the optional insight |
| Fallback | GroqCloud | `openai/gpt-oss-120b` | SQL only, when Gemini cannot answer |

```
question → Gemini SQL ──(success)──────────────────────────┐
              └─(eligible failure)→ GroqCloud SQL, once ───┤
                                                           ↓
               the same SQLGlot validator → the same read-only executor → chart and table → History
```

The fallback is available only when `GROQ_API_KEY` is set; without it DataPilot is Gemini-only, as
before. There is no separate on/off setting. `query_service.run_business_query` routes it;
`groq_sql.generate_sql` sends GroqCloud the same system prompt and question as Gemini (one shared
copy in `nl_to_sql.py`) and returns SQL text only.

| Gemini failure | GroqCloud tried? |
|---|---|
| `rate_limited` (429) | yes |
| `unavailable` (5xx or network) | yes |
| `timeout` (20 s) | yes, if the remaining budget allows |
| `model_unavailable`, `not_configured` (model or key problem) | yes |
| `invalid_response`, `empty_response` | yes |
| `request_failed` (another 4xx), `invalid_question` | no |
| SQL rejected by the validator, unsafe SQL, any database error | **never** |

If Gemini succeeds, GroqCloud is not called. GroqCloud gets exactly one attempt and never hands back
to Gemini. Once SQL reaches the validator, provider fallback is over.

**Time budget** (cooperative: each call has its own timeout, and a step starts only if it still
fits):

| | Gemini-only | With GroqCloud |
|---|---|---|
| Request budget | 45 s | 50 s |
| Gemini SQL | 20 s per attempt, transient retries as above | 20 s, one attempt |
| GroqCloud SQL | n/a | 10 s, one attempt; starts only if 26 s remain (10 s + 15 s database reserve + 1 s margin) |

Both budgets stay below the frontend's 60 s timeout. A Gemini timeout after its full 20 s leaves
30 s of the 50 s, so the fallback still fits.

## 4. Backend Structure

All backend code is in `backend/app/`.

| Module | Responsibility |
|---|---|
| `main.py` | The FastAPI app: `GET /health`, `POST /query`, `POST /auth/login`, `GET /auth/session`, `POST /auth/logout`, the History and Saved Reports endpoints (`GET /history`, `GET /history/{id}`, `POST /saved-reports`, `GET /saved-reports`, `GET /saved-reports/{id}`, `DELETE /saved-reports/{id}`), the request models, middleware order, exception handlers that map every error to `{"error": {"code", "message"}}`, and the rate-limit dependencies |
| `auth.py` | Private demo access: one shared login checked by the backend, short-lived HMAC-SHA256-signed bearer tokens (`v1.<expiry>.<session id>.<signature>`), in-memory logout revocation, and the `require_session` dependency that `/query` runs before anything else |
| `config.py` | `Settings` (pydantic-settings): database URL, Gemini key and model, the optional Groq key and model, CORS origins, rate limit, timeouts, row cap. Secrets are `SecretStr`, so they never appear in logs |
| `query_service.py` | The pipeline, `run_business_query`: generate (Gemini, then the GroqCloud fallback when configured) → validate → execute → select visualization → optional insight. Holds the fallback rules (`FALLBACK_KINDS`) and the request budgets. Defines `QueryResponse` and turns step failures into `QueryServiceError` kinds |
| `nl_to_sql.py` | The Gemini SQL provider and the shared SQL prompt: schema context, business definitions, SQL rules, untrusted-input rules, `generate_sql`, and `SQLGenerationError` |
| `groq_sql.py` | The GroqCloud SQL provider: `generate_sql` sends the shared prompt to Groq's OpenAI-compatible Chat Completions API with httpx (structured JSON output, 10-second timeout, one attempt) and raises the same `SQLGenerationError` kinds. It only returns SQL text; it never validates or runs it |
| `sql_validator.py` | `validate_sql`: the SQLGlot safety checks and `LIMIT` enforcement. No network or database access, so it is tested on its own |
| `db_executor.py` | `execute_query`: runs approved SQL as the read-only role over TLS (always `sslmode=require` or stricter), with timeout and row and size caps; `to_json_value` converts PostgreSQL types; `QueryExecutionError` hides connection details |
| `history_store.py` | DataPilot's own data, as the `datapilot_app` role (`APP_DATABASE_URL`, TLS, 3-second statements, fixed SQL with bound parameters). `record_analysis` stores a successful answer and never raises (`saved` with the new id, `disabled` or `failed`); `list_history`, `get_analysis`, `save_report`, `list_saved_reports` and `get_saved_report` serve the API, scoped to an account, and raise `HistoryUnavailable` if the database cannot be used. The response models (`StoredAnalysis`, `AnalysisSummary`, ...) validate stored records before they are returned |
| `chart_selector.py` | `select_visualization` and the `Visualization` model (`type`, `x_key`, `y_key`) |
| `insight_service.py` | `generate_insight`: the optional summary prompt, a 15-second timeout, output length checks, and `InsightError` |
| `middleware.py` | Plain ASGI middleware: `RequestContext` (request ID and summary log line), `LimitRequestBody` (16 KB), `CatchUnexpectedErrors` (safe 500s that still carry CORS headers), `SecurityHeaders`, and `error_response` |
| `request_log.py` | Logging setup, request IDs and `QueryMetrics`: the outcome, stage timings, Gemini attempt count and SQL provider and fallback outcome of one request, logged as a single `key=value` line |
| `client_ip.py` | `client_ip`: the rate-limit identity, from `CF-Connecting-IP` when `TRUST_CF_CONNECTING_IP` is on, otherwise the TCP peer. `X-Forwarded-For` is never used |
| `rate_limiter.py` | `RateLimiter`: an in-memory sliding window per client; `DailyLimit`: an in-memory count of questions per day (midnight to midnight Pacific time, like Gemini's quota) across all clients |

Middleware runs in this order for each request, outermost first: `SecurityHeaders` → CORS →
`RequestContext` → `CatchUnexpectedErrors` → `LimitRequestBody` → the route.

Setup scripts live in `database/` (`schema.sql`, `seed.py`, `readonly_role.sql`,
`create_readonly_role.py`, `apply_schema.py`, `api_roles.sql`, `revoke_api_roles.py`, and for
History and Saved Reports `app_schema.sql`, `app_role.sql`, `create_app_role.py`). They use
the admin connection and are never part of the running API.

## 5. Frontend Structure

The frontend (`frontend/src/`) is a single page with plain React state; no router or state library.

| File | Responsibility |
|---|---|
| `AccessGate.jsx` | Checks the demo session, shows the login page, then the signed-in app |
| `App.jsx` | The signed-in views (Ask, History, Saved Reports) and the question and answer state; prevents double submits |
| `services/api.js` | The only module that calls the backend: login and session, `postQuery`, the History and Saved Reports calls (including `deleteSavedReport`), the timeout, and mapping HTTP errors to friendly messages |
| `hooks/useSavedReports.js` | The saved-report count, which analyses are saved, saving and removing |
| `components/AppShell.jsx` | The sidebar (an icon rail on narrow screens) and the top bar |
| `components/LoginPage.jsx`, `BootLoader.jsx` | The login page and the session-check loader |
| `components/AskHome.jsx`, `QuestionForm.jsx`, `AnalysisProgress.jsx` | The Ask page, the question box and example questions, and the progress shown while a question is answered |
| `components/ResultView.jsx` | One answer: header, summary tiles, the chart, the insight, the data and the SQL |
| `components/HeroChart.jsx` | Draws exactly what the backend chose (KPI, bar or line, with Recharts); it never picks a chart or columns itself |
| `components/InsightCard.jsx`, `ResultsTable.jsx`, `QuestionHeader.jsx` | The AI insight (or the quiet unavailable note), the raw result table with its row count and truncation note, and the question heading |
| `components/SqlViewer.jsx` | The generated SQL with a Copy SQL button |
| `components/ErrorMessage.jsx` | The safe error card |
| `components/HistoryPage.jsx`, `SavedReportsPage.jsx` | The History and Saved Reports lists, search, and reopened analyses and reports |
| `components/SaveReportAction.jsx`, `SaveReportDialog.jsx`, `RemoveReportAction.jsx` | Saving an analysis under a title, and removing a saved report after an inline confirmation |
| `components/ExportPdfAction.jsx`, `ExportPptxAction.jsx` | The Export PDF and Export PPTX buttons (see "Report export" below) |
| `pdf/`, `pptx/`, `utils/exportFile.js` | The PDF and PowerPoint exports |
| `utils/format.js`, `result.js`, `history.js`, `savedReports.js`, `reportTitle.js` | Display formatting (₹ for money-like columns, % for percentage columns, labels, dates), result summaries, and the History, report and title helpers |
| `config/` | The fixed Ask-page text and the demo workspace labels |

### Report export (PDF and PowerPoint)

Export PDF and Export PPTX appear under a result in three places: a fresh answer, a reopened History
analysis and a reopened saved report. Both files are made **in the browser** from the analysis
already on screen (for History and Saved Reports, the stored snapshot). Nothing is sent to the
backend, Gemini, GroqCloud or the database, and nothing is recalculated.

- **Shared content.** `pdf/reportContent.js` (`buildReportContent`) turns the analysis into the
  report both files show, so the PDF and the deck carry the same values. `utils/exportFile.js` draws
  the logo to a PNG and saves the file through a temporary `blob:` link. The file is named
  `datapilot-<a few words of the title>-<YYYY-MM-DD>.pdf` or `.pptx`.
- **PDF** (`pdf/`, jsPDF). An A4 report drawn from text and vector shapes, not a screenshot of the
  page. It embeds subsets of Noto Sans fonts so that ₹ prints correctly.
- **PowerPoint** (`pptx/`, PptxGenJS). `buildSlides.js` plans the slides without drawing anything:
  a Summary slide (the question and the summary figures), a Chart slide for bar and line results,
  an Insight slide only when the insight is too long to sit beside the summary (results with no
  chart) or beside the chart, as many Data slides as the rows need, and a Generated SQL slide.
  `renderDeck.js`
  draws the plan as native PowerPoint content: text, a real table and real charts that stay
  editable (right-click, Edit Data). Every slide carries the DataPilot header, a footer and "n of
  N". No sidebar, buttons or other app chrome can appear.
- **Loaded on demand.** Each exporter is its own chunk, fetched only the first time its button is
  pressed, so neither library weighs on the first page load.
- **No Content-Security-Policy change.** The code needs no `eval` or `Function`, the logo is the
  same-origin SVG, and the download is a `blob:` link. `frontend/vercel.json` is unchanged, and the
  backend and Render are untouched by either export.

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

**Application data (History and Saved Reports, live in production):** DataPilot's own data lives apart from the business data, in schema `datapilot` (`database/app_schema.sql`):

| Table | Holds |
|---|---|
| `datapilot.analyses` | one row per successful analysis: a snapshot of the question, the SQL that ran, the columns and rows, row count, truncation flag, chart spec and insight, with `account_id` and `created_at` |
| `datapilot.saved_reports` | an analysis the user chose to keep: a title and a reference to the analysis (at most one report per analysis), never a copy of it |

It is written by a second role, `datapilot_app` (`database/app_role.sql`), with its own connection
string, `APP_DATABASE_URL`: `SELECT` and `INSERT` on those two tables, `DELETE` on
`datapilot.saved_reports` only (to remove a saved report), no `UPDATE`, and no access to the
business tables. `datapilot_readonly`, which runs the generated SQL, has no
access to schema `datapilot`. `schema.sql` never touches it, so reseeding the business data keeps
every analysis and saved report. The schema and role exist in the hosted database and are verified
by live tests.

**Automatic History recording:** after `run_business_query` has
returned a successful answer, the `/query` endpoint stores it with `history_store.record_analysis`
and returns the new row's id as `analysis_id`. Only complete answers are stored, including those
with no rows or no insight, and answers whose SQL came from GroqCloud; any request that fails
(login, validation, rate limits, SQL generation, validator, database) stores nothing. A fallback
answer is stored once, like any other. The snapshot is exactly what the user received, so reopening it
later never calls an AI provider or reruns the SQL. Records belong to the account, not the session: the one shared
demo login is account `demo` (`auth.DEMO_ACCOUNT_ID`), so History survives logout and new sessions.
History is secondary to the answer: if storing fails or `APP_DATABASE_URL` is not set, the full
answer is still returned with HTTP 200 and `analysis_id: null`.

**History and Saved Reports API:** every endpoint
needs a demo session (checked first), then has its own limit of 60 requests per minute per client,
separate from the `/query` limits and the daily cap. Everything is read from the stored snapshots:
no endpoint calls an AI provider, regenerates SQL, runs a stored query or uses the daily cap.

| Endpoint | Returns |
|---|---|
| `GET /history?limit=50` (1-100) | `{"items": [{"id", "question", "visualization_type", "row_count", "truncated", "created_at", "saved_report_id"}]}`, newest first; no rows or SQL |
| `GET /history/{analysis_id}` | `{"id", "question", "sql", "columns", "rows", "row_count", "truncated", "visualization", "insight", "created_at", "saved_report": {"id", "title"} or null}`, the same field names as a `/query` answer |
| `POST /saved-reports` with `{"analysis_id", "title"}` | 201 `{"id", "analysis_id", "title", "saved_at"}`. The title is trimmed, 1-120 characters, no control characters; no other fields are accepted, so a report's content is always the stored analysis |
| `GET /saved-reports?limit=50` (1-100) | `{"items": [{"id", "analysis_id", "title", "question", "visualization_type", "row_count", "truncated", "saved_at", "analysis_created_at"}]}`, most recently saved first |
| `GET /saved-reports/{report_id}` | `{"id", "title", "saved_at", "analysis": {...the stored analysis...}}` |
| `DELETE /saved-reports/{report_id}` | 204, no body. Removes only the report: its analysis stays in History and can be saved again |

Errors: 404 `analysis_not_found` or `report_not_found` (the same for an id that does not exist and one
that belongs to another account), 409 `already_saved` (one report per analysis), 400
`invalid_request` (malformed id, limit or title), 429 `too_many_requests`, and 503
`history_unavailable` whenever the History database cannot be used, including when
`APP_DATABASE_URL` is not set: an empty list would wrongly say there is no History.
Connections are capped in the process at `datapilot_app`'s limit of 5, of which the API may use 4,
so one is always free to record a `/query` answer; an API request that waits 5 s for a free
connection gets the same 503 (`cause=busy`). The cap assumes the one uvicorn worker in
`render.yaml`; more workers or instances would each need a share of the role's limit. Nothing can be
edited, and History is never deleted: `datapilot_app` has `SELECT` and `INSERT` on both tables and
`DELETE` only on `datapilot.saved_reports`. Timestamps are ISO 8601 in UTC.

**Production status:** live since Phase 19, with `APP_DATABASE_URL` set on Render; production
storage and every Phase 19 endpoint were verified after the rollout. Report removal
(`DELETE /saved-reports/{id}`) came later and was checked in the live app too: the report is
removed from Saved Reports and the count updates, while its analysis stays in History and can be
saved again. The one shared demo login is one account,
so every demo visitor sees the same History and Saved Reports. The frontend (UI V2) has a History
screen and a Saved Reports screen that read these endpoints.

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
| AI provider | Gemini API | Primary: SQL and the optional insight. Called only by the backend |
| AI fallback | GroqCloud API | SQL only, when Gemini cannot answer and `GROQ_API_KEY` is set. Called only by the backend |

- The browser calls the Render API directly; Vercel serves static files only.
- `CORS_ALLOWED_ORIGINS` allows the production Vercel origin plus the localhost development origins.
- The Gemini key, the Groq key and the database URLs exist only as Render environment variables.
  The frontend bundle contains no secrets and never learns which provider wrote the SQL.
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
- **One controlled fallback (GroqCloud)**: one GroqCloud attempt with the same prompt keeps
  questions answerable when Gemini is rate-limited or unavailable. It is a single fallback for SQL
  generation, not a multi-model system: no routing by question, no comparing answers, and no second
  model after the validator rejects SQL. Called over plain httpx, with no extra SDK.
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
| Gemini rate-limits SQL generation | With GroqCloud: GroqCloud is tried. Gemini-only, or with no time left for GroqCloud: HTTP 429 `rate_limited`, never retried; a `Retry-After` header when Gemini sends a usable retry delay, and a more specific message when its quota metadata shows a temporary limit or an exhausted quota |
| Gemini unavailable (5xx or network), or its model or key unusable | With GroqCloud: GroqCloud is tried at once, without a Gemini retry. Gemini-only: 5xx and network errors are retried twice, then HTTP 503 `generation_unavailable` |
| Gemini does not answer within 20 s | With GroqCloud, if 26 s of the budget remain: GroqCloud is tried. Otherwise HTTP 503 `generation_timeout` |
| Gemini returns an empty or malformed answer | With GroqCloud: GroqCloud is tried. Gemini-only: HTTP 502 `generation_failed` |
| Gemini rejects the request (another 4xx) | HTTP 502 `generation_failed`; never passed to GroqCloud |
| Gemini failed and GroqCloud fails too (any reason, including its own 429) | HTTP 503 `generation_unavailable` with the usual neutral message and no `Retry-After`; Gemini is not called again |
| GroqCloud wrote the SQL | Validated and run exactly like Gemini's; HTTP 200 without the optional insight |
| Generated SQL fails validation | HTTP 400 `unsafe_sql`; the SQL is never executed |
| Database refuses the query (permissions) | HTTP 400 `query_not_allowed` |
| Query error (for example an unknown column) | HTTP 422 `query_failed` |
| Result larger than `MAX_RESULT_BYTES` | HTTP 422 `result_too_large`; nothing partial is returned and no insight is requested |
| Query exceeds the statement timeout | HTTP 504 `query_timeout` |
| Database unreachable | HTTP 503 `database_unavailable` |
| Insight generation fails for any reason | HTTP 200 with the full result and `insight: null` |
| Storing History fails, or `APP_DATABASE_URL` is not set | HTTP 200 with the full result and `analysis_id: null`; logged as `history_status=failed` or `disabled`, never with connection details |
| History or Saved Reports API cannot use the database (or `APP_DATABASE_URL` is not set) | HTTP 503 `history_unavailable`; logged as `event=history_unavailable` with the endpoint and a fixed cause only |
| No valid demo session (missing, expired, revoked or forged token) | HTTP 401 `unauthorized`; the frontend returns to the login page. No rate-limit slot, daily-cap unit, Gemini call or query is used |
| Wrong demo username or password | HTTP 401 `invalid_credentials`, the same for either |
| Too many login attempts from one client | HTTP 429 `too_many_login_attempts` with a `Retry-After` header |
| Too many questions from one client | HTTP 429 `too_many_requests` with a `Retry-After` header |
| Daily question cap reached (all clients) | HTTP 429 `daily_limit_reached`, `Retry-After` until midnight Pacific time; the pipeline is not called |
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
event=query_complete request_id=c3f18a7d25e04b96 outcome=success status=200 question_length=26 total_ms=3240 gemini_sql_attempts=1 sql_provider=groq fallback=success fallback_reason=unavailable sql_ms=2710 validation_ms=3 db_ms=402 rows=1 truncated=false visualization=kpi insight_status=skipped_fallback history_status=saved history_ms=95
```

- `error_kind` is the code the client received; `stage` (`request`, `app_rate_limit`, `sql`,
  `validation`, `db`, `internal`, `auth`) and `cause` say where and why. Causes include `app_rate_limited`
  (DataPilot's per-client limit) and `global_daily_limit` (its daily cap) versus `gemini_rate_limited`, `gemini_unavailable`, `gemini_timeout`,
  `gemini_invalid_response`, `groq_<kind>` (both providers failed, for example `groq_timeout`),
  `validator_rejected`, `db_timeout`, `db_unavailable`, `db_query_error` and
  `db_result_too_large`; an unexpected exception gives its type.
- `gemini_sql_attempts` is counted as attempts happen; `sql_ms` includes the waits between retries
  and any GroqCloud attempt.
- `sql_provider` is `gemini` or `groq`: which provider wrote the SQL that was validated. Only when
  GroqCloud is configured, `fallback` is `not_used`, `success`, `failed` or `skipped_budget`, and
  `fallback_reason` is the Gemini error kind that made GroqCloud eligible.
- `insight_status` is `success`, `failed` (with `insight_error`), `skipped_budget`,
  `skipped_empty` or `skipped_fallback` (GroqCloud wrote the SQL).
- `history_status` (successful answers only) is `saved`, `disabled` (no `APP_DATABASE_URL`) or
  `failed` (with `history_error`: `busy`, `unavailable`, `timeout`, `insert_failed`,
  `invalid_configuration`, or an unexpected exception's type), plus `history_ms`. A `failed` History
  write keeps `outcome=success` but logs the line at WARNING. The `analysis_id` is not logged.

A History or Saved Reports request that gets 503 logs one WARNING line,
`event=history_unavailable request_id=... endpoint=history_detail cause=timeout`: the endpoint's
name and the store's error kind only, never ids, titles, questions, SQL or connection details.
- Timings are whole milliseconds from a monotonic clock. Fields that do not apply are left out.

The line never contains the question (only its length), the generated SQL, result rows, client
addresses, connection strings, API keys, `Authorization` headers or provider error bodies.
Provider (Gemini, GroqCloud) and validator messages are not logged either, because they can quote
the question or the SQL. Library loggers such as
httpx stay at WARNING, so request URLs are not logged.
