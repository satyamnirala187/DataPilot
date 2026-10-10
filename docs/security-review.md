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
| 10 | Secrets stay server-side | Met | `.env` is git-ignored; `SecretStr` settings; the frontend only has `VITE_API_BASE_URL` | full git-history scan (Phase 11): no keys, URLs or `.env` files committed; production (Phase 13): secrets are set only as Render environment variables, and a scan of the deployed frontend bundle found none |
| 11 | Restricted CORS | Met | `CORS_ALLOWED_ORIGINS` setting (default: localhost Vite); no `*`, no credentials | `test_api.py` and `test_security.py` CORS tests, including unexpected 500s; production (Phase 13): the Vercel origin is configured and the live frontend calls the API successfully |
| 12 | Input limits | Met | `QueryRequest`: 1–500 characters, unknown fields rejected; 16 KB request body cap | `test_invalid_request_bodies_are_rejected_before_the_pipeline`, oversized-body tests |
| 13 | Safe error messages | Met | one `{"error": {code, message}}` shape for every error; `CatchUnexpectedErrors`; sanitized Gemini and database errors | `test_unexpected_500_is_safe_...`, Gemini/database sanitization tests, framework 404/405 tests |
| 14 | Basic rate limiting | Met | `RateLimiter` on `POST /query`: 5 per 60 s per client IP (configurable); `/health` not limited | `tests/test_rate_limiter.py`, rate-limit tests in `test_security.py` |
| 15 | Prompt-injection awareness | Met | the question is wrapped as untrusted data in both Gemini prompts; rules 1–9 apply whatever the model returns | prompt tests in `test_nl_to_sql.py` / `test_insight_service.py`; compromised-model pipeline tests |

## Known limits (accepted for v1)

- The rate limiter is in memory, so each backend process counts separately and a restart clears
  it. Behind Render's proxy every request comes from a private proxy address, so clients are
  identified by Cloudflare's `CF-Connecting-IP` (`app/client_ip.py`, enabled with
  `TRUST_CF_CONNECTING_IP=true`), never by the client-controlled `X-Forwarded-For`. See
  `docs/deployment.md` for how to verify it after a deploy.
- Phase 8 is complete; its production `/docs` check used a normal revenue question. A live
  prompt-injection check through Gemini has not been run yet, because of Gemini rate limits. The
  tests above assume the worst case instead: a model that obeys the injection completely.
