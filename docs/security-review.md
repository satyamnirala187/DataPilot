# Security requirements review (Phase 11)

Each of the 15 Security Requirements in `PROJECT.md` §9, where it is enforced, and the tests
that prove it. Reviewed on 2026-10-09 against the code at the end of Phase 11.

Status: **Met** = enforced in code and tested. Production deployment (Phase 13) is complete, and
the deployment-dependent requirements (10 and 11) were re-checked in production.

## SQL validation (application layer, SQLGlot)

| # | Requirement | Status | Where | Evidence |
|---|---|---|---|---|
| 1 | Single statement only | Met | `sql_validator._parse_single_statement` | `tests/test_sql_validator.py`; `test_compromised_llm_output_is_blocked_before_the_database` (`SELECT 1; DELETE ...`) |
| 2 | Read-only only, including inside CTEs | Met | `sql_validator._check_read_only` (`FORBIDDEN_NODES`) | validator tests; pipeline test with `WITH gone AS (DELETE ...)`, `FOR UPDATE`, `SELECT ... INTO` |
| 3 | Table allowlist, no system schemas | Met | `sql_validator._check_tables` (`ALLOWED_TABLES`, `ALLOWED_SCHEMAS`) | validator tests; pipeline tests for `pg_catalog` and `information_schema` |
| 4 | Dangerous function blocklist | Met | `sql_validator._check_functions` (blocklist, `pg_`/`lo_`/`dblink` prefixes, unknown functions rejected) | validator tests; pipeline tests for `pg_sleep`, `pg_read_file` |
| 5 | Row limit enforced | Met | `sql_validator._enforce_limit` (adds or caps `LIMIT 501`: 500 rows plus a truncation probe); executor returns at most `max_result_rows` (500) | `test_the_validator_adjusted_sql_is_what_gets_executed`; executor row-cap tests |
| 6 | Fail closed | Met | parse errors, unknown functions and unknown statements are rejected | validator tests |

The pipeline (`query_service.run_business_query`) passes only the validator's output to the
executor. `tests/test_security.py` feeds the real validator SQL that a fully prompt-injected model
could return and checks that the executor is never called.

## Database layer (defence in depth)

| # | Requirement | Status | Where | Evidence |
|---|---|---|---|---|
| 7 | Read-only database role | Met | `database/readonly_role.sql` (SELECT on the six tables only); the app only knows `READONLY_DATABASE_URL` | live: `test_writes_are_refused_even_without_the_validator`, `test_tables_outside_the_six_are_not_readable`; unit: `test_admin_database_url_is_not_an_app_setting`, `test_queries_use_the_read_only_url_...` |
| 8 | Read-only transactions | Met | `db_executor.execute_query`: `conn.read_only = True`, `transaction(force_rollback=True)` | `test_queries_use_the_read_only_url_and_a_read_only_rolled_back_transaction` |
| 9 | Statement timeout | Met | `set_config('statement_timeout', ...)` in every transaction (`QUERY_TIMEOUT_MS`, default 5 s) | live: `test_slow_query_is_cancelled_by_the_statement_timeout`; unit: same transaction test |

## Application and deployment

| # | Requirement | Status | Where | Evidence |
|---|---|---|---|---|
| 10 | Secrets stay server-side | Met | `.env` is git-ignored; `SecretStr` settings (including the optional `GROQ_API_KEY`); the frontend only has `VITE_API_BASE_URL` | full git-history scan (Phase 11): no keys, URLs or `.env` files committed; production (Phase 13): secrets are set only as Render environment variables, and a scan of the deployed frontend bundle found none |
| 11 | Restricted CORS | Met | `CORS_ALLOWED_ORIGINS` setting (default: localhost Vite); no `*`, no credentials | `test_api.py` and `test_security.py` CORS tests, including unexpected 500s; production (Phase 13): the Vercel origin is configured and the live frontend calls the API successfully |
| 12 | Input limits | Met | `QueryRequest`: 1–500 characters, unknown fields rejected; 16 KB request body cap | `test_invalid_request_bodies_are_rejected_before_the_pipeline`, oversized-body tests |
| 13 | Safe error messages | Met | one `{"error": {code, message}}` shape for every error; `CatchUnexpectedErrors`; sanitized Gemini, GroqCloud and database errors | `test_unexpected_500_is_safe_...`, Gemini/database sanitization tests, `tests/test_groq_sql.py` and `tests/test_sql_fallback.py` (no key or provider body in errors or logs), framework 404/405 tests |
| 14 | Basic rate limiting | Met | `RateLimiter` on `POST /query`: 5 per 60 s per client IP (configurable); `/health` not limited | `tests/test_rate_limiter.py`, rate-limit tests in `test_security.py` |
| 15 | Prompt-injection awareness | Met | the question is wrapped as untrusted data in both Gemini prompts and in the same SQL prompt sent to the GroqCloud fallback; rules 1–9 apply whatever either model returns | prompt tests in `test_nl_to_sql.py` / `test_insight_service.py` / `test_groq_sql.py`; compromised-model pipeline tests, including unsafe GroqCloud SQL in `test_sql_fallback.py` |

## Known limits (accepted for v1)

- The rate limiter is in memory, so each backend process counts separately and a restart clears
  it. Behind Render's proxy every request comes from a private proxy address, so clients are
  identified by Cloudflare's `CF-Connecting-IP` (`app/client_ip.py`, enabled with
  `TRUST_CF_CONNECTING_IP=true`), never by the client-controlled `X-Forwarded-For`. See
  `docs/deployment.md` for how to verify it after a deploy.
- Phase 8 is complete; its production `/docs` check used a normal revenue question. Both live
  safety benchmark cases (a delete request and a prompt injection to drop `orders`) passed through
  Gemini on 2026-10-15: each returned the "cannot be answered" message and no table's row count
  changed (`docs/benchmark.md` §9). The tests above still assume the worst case: a model that obeys
  the injection completely.
- Logout revocations, like the rate limits, are kept in memory: a backend restart forgets them
  (`docs/deployment.md`, "Rotating access").
- One shared demo login means every visitor shares the same History and Saved Reports. Saved report
  titles typed by one visitor are shown to others, always as plain text.
- The frontend loads its fonts from Google Fonts; if that service is unavailable the interface
  falls back to system fonts.
- The production JavaScript bundle is about 665 kB (196 kB gzipped), above Vite's 500 kB warning.
  This affects load time, not security. The PDF (about 516 kB) and PowerPoint (about 379 kB)
  exporters are separate chunks, fetched only when their button is first pressed.
- The CSP's `connect-src` names the Render API origin, so a new backend URL needs both
  `VITE_API_BASE_URL` and `frontend/vercel.json` updated (`docs/deployment.md`).

Not vulnerabilities, but current product limits: saved reports can be removed but not renamed
(`DELETE /saved-reports/{id}` removes only the report; History is never deleted). PDF and PPTX
export (fresh answers, History, Saved Reports) are generated client-side from the analysis already
on screen or its stored snapshot: they make no Gemini, GroqCloud, backend or database call, and
need no CSP change. The PDF embeds its fonts instead of fetching them. The PPTX code needs no
`eval` or `Function` constructor (`script-src 'self'` stays as it is), embeds only the logo (as a
data URL) and is covered by tests that fail if it calls `fetch` or `XMLHttpRequest`.

**PDF export production check, 2026-10-19.** The project owner exported a stored production
analysis (12 result rows, bar chart) as a PDF from the live site. The real PDF downloaded, with:

- DataPilot branding, the summary/KPI content and ₹ formatting correct;
- the bar chart correct, and all 12 result rows present across 2 pages;
- the AI insight and the generated SQL present, and page numbers correct;
- no application chrome (buttons, navigation or sidebar) in the PDF.

**PPTX export production check, 2026-10-20.** The project owner exported a stored production
History analysis (12 result rows, bar chart) as a PowerPoint file from the live site. Vercel's
Production deployment for commit `bc823e8` had completed, and the live PPTX chunk matched a local
build of that commit. The real `.pptx` downloaded, and opened and rendered cleanly with no blocking
layout issue, with:

- DataPilot branding, and the question and timestamps correct;
- the summary cards and ₹ formatting correct;
- a native, editable bar chart;
- the AI insight, all 12 result rows and the generated SQL present;
- slide numbers correct;
- no application chrome (buttons, navigation or sidebar) in the deck.

No backend (Render) deployment was involved: the export is client-side only.

Accepted v1 limitations of the PowerPoint export, not security issues: the labels of native charts
may use Western digit grouping (1,234,567) while the deck's text and tables use Indian grouping
(12,34,567), and Keynote may fail to import a native line chart that has a gap (a missing value).
The file itself is correct and renders in PowerPoint and other Office-compatible viewers. Charts
stay native and editable; they are not replaced by pictures.

## Production verification (Phase 17 closure)

Checked on 2026-10-15, after UI V2 was merged to `main` and deployed:

- **Frontend deployed:** the live site serves the UI V2 build from `main`.
- **Security headers live** on `https://data-pilot-flax.vercel.app`, as set in
  `frontend/vercel.json`: `Content-Security-Policy`, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and `Permissions-Policy`
  (camera, microphone, geolocation, payment and USB off).
- **CSP enforced, not only present:** no violations on the live page at desktop and phone widths,
  Google Fonts loading, and framing by another site refused (`frame-ancestors 'none'`).
- **HSTS** is supplied by Vercel: `max-age=63072000; includeSubDomains; preload`. No separate
  project HSTS header is needed.
- **Owner smoke test in production:** login, one answered question, the History list with a stored
  analysis reopened and Back, and the Saved Reports list with a saved report reopened and Back.

Phase 17 (security, legal and release hardening) is closed.

## Second AI provider: GroqCloud SQL fallback

Added after Phase 17. GroqCloud (`openai/gpt-oss-120b`) is a second external AI provider, used only
to write SQL when Gemini cannot (`docs/architecture.md`, "SQL-generation fallback"). It is enabled
only when `GROQ_API_KEY` is set on the backend.

**What GroqCloud receives** (the same SQL-generation request Gemini receives):

- the user's natural-language question (1–500 characters), inside the untrusted-input delimiters;
- DataPilot's schema context: the six business tables, their columns and relationships;
- the business definitions and SQL rules needed to write the query.

**What it never receives:** database credentials or connection strings, the Gemini API key, or any
database result rows (the fallback path is SQL generation only; the insight, the one prompt that
contains rows, goes to Gemini and is skipped when GroqCloud wrote the SQL). `GROQ_API_KEY` is sent
to GroqCloud only as the `Authorization` header that authenticates the request, never as part of
the prompt.

**Controls:**

- GroqCloud only returns SQL text. Its SQL goes through the same SQLGlot validator, the same
  read-only role, read-only transaction, statement timeout and row and size limits as Gemini's;
  there is no separate execution path.
- Fallback happens only before validation, for failures that mean Gemini produced no usable SQL. A
  validator rejection or a database error never leads to another model, so a question cannot shop
  between providers for SQL that gets past the checks.
- One GroqCloud attempt per question, with a 10-second timeout, inside the request budget; the
  per-client limit and the global daily cap apply unchanged.
- `GROQ_API_KEY` is a `SecretStr` setting, set only in Render's environment and the local `.env`;
  it is never a `VITE_` variable and never in the frontend. Tests blank the real key and use fakes.
- Provider errors are reduced to a status and a fixed error code; GroqCloud's error messages,
  request details and the key are never logged or returned. Both providers failing gives the
  usual neutral 503.

This review does not assess GroqCloud's own data-retention or privacy terms.

**Production smoke test, 2026-10-17.** With `GROQ_API_KEY` set on Render, one controlled question
was answered in production through the fallback. This is a single check, not a benchmark. The
request's summary log line showed:

- outcome success, HTTP 200, after one Gemini SQL attempt that failed as `unavailable`;
- `fallback=success` and `sql_provider=groq`: GroqCloud wrote the SQL;
- the SQL passed validation and ran on the read-only role, returning one row shown as a KPI;
- `insight_status=skipped_fallback`: the Gemini insight was skipped, as designed;
- `history_status=saved`: the answer was stored once in History;
- total request time 7.825 s.
