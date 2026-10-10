# Deployment

DataPilot runs as a static frontend on **Vercel**, one API service on **Render**, and the existing
**Supabase** PostgreSQL database.

```
Browser ──> Vercel (static React app)
   │
   └──── HTTPS JSON ───> Render (FastAPI, 1 process) ──> Supabase PostgreSQL (read-only role)
                                │
                                └──> Gemini API
```

The browser only ever talks to the Render API. Secrets (Gemini key, database URL) live only in
Render's environment; the frontend holds just the public API URL.

## Prerequisites

- The GitHub repository with this code pushed to `main`.
- The Supabase database already has the schema, seed data and the `datapilot_readonly` role
  (`database/`). It is reused as is; nothing is recreated or reseeded.
- The read-only connection string from Supabase, using the **session pooler** (IPv4). Render cannot
  reach Supabase's IPv6-only direct host (`db.<ref>.supabase.co`).
- A Gemini API key.

## 1. Backend on Render

Create the service from the Blueprint (`render.yaml`, **New → Blueprint**), or by hand
(**New → Web Service**) with the same values:

| Setting | Value |
|---|---|
| Runtime | Python 3 |
| Root directory | `backend` |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 --no-proxy-headers` |
| Health check path | `/health` |
| Instance | one instance (the free plan is fine) |
| Python version | environment variable `PYTHON_VERSION=3.13.16` (not a secret) |
| Client IPs | environment variable `TRUST_CF_CONNECTING_IP=true` (not a secret, see [Client IPs](#security-notes)) |

Environment variables (names only; enter values in the Render dashboard):

| Name | Required | Value |
|---|---|---|
| `READONLY_DATABASE_URL` | yes | read-only role via the session pooler, ending in `?sslmode=require` |
| `GEMINI_API_KEY` | yes | Gemini API key |
| `CORS_ALLOWED_ORIGINS` | yes | JSON list, see [CORS](#cors) |
| `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS` | no | default 5 questions per 60 s per client |
| `GLOBAL_DAILY_QUERY_LIMIT` | no | default 5 questions per Pacific day from all clients; set to `5` on Render. Size it to the Gemini quota: one question can use up to 4 Gemini requests |
| `GEMINI_MODEL` | no | default `gemini-3.7-flash`; set explicitly on Render |

Never set the admin `DATABASE_URL` on Render: it is only for the setup scripts in `database/`.

`PYTHON_VERSION` takes precedence over any other Python version setting on Render, so the build
uses exactly Python 3.13.16; the build log shows the version it installed. The Blueprint sets it
for you; for a hand-made service, add it with the other environment variables.

The Render Free web-service plan can spin down after inactivity, so the first request after an
idle period may have a noticeable cold start. This is acceptable for the portfolio/demo deployment.

Then open `https://<service>.onrender.com/health`; it should return
`{"status":"ok","service":"DataPilot API"}`. The health check never calls Gemini or the database.

## 2. Frontend on Vercel

**Add New → Project**, import the repository, then:

| Setting | Value |
|---|---|
| Root directory | `frontend` |
| Framework preset | Vite (detected) |
| Build command | `npm run build` |
| Output directory | `dist` |
| Environment variable | `VITE_API_BASE_URL=https://<service>.onrender.com` (no trailing slash) |

No `vercel.json` is needed: the app is a single static page with no client-side routes or
serverless functions. `VITE_API_BASE_URL` is compiled into the bundle, so it must be set before
the build, and changing it needs a redeploy. It is public by design.

## CORS

`CORS_ALLOWED_ORIGINS` is a **JSON list** of exact origins (scheme + host, no path, no trailing
slash). Keep the localhost origins for development:

```
["https://<project>.vercel.app","http://localhost:5173","http://127.0.0.1:5173"]
```

A comma-separated value is rejected at startup. `*` is never used. Only the production Vercel URL
is listed, so Vercel preview deployments (different URLs) cannot call the API.

## Deployment order

1. Push the deployment configuration to GitHub.
2. Deploy the backend on Render, with `CORS_ALLOWED_ORIGINS` set to the localhost origins for now.
3. Check `/health` on the Render URL.
4. Copy the Render URL.
5. Set `VITE_API_BASE_URL` to it in Vercel.
6. Deploy the frontend on Vercel.
7. Copy the Vercel production URL.
8. Add it to `CORS_ALLOWED_ORIGINS` on Render.
9. Save; Render restarts the service with the new value.
10. Ask a question on the Vercel URL: frontend → Render → Supabase (and Gemini).

## Security notes

Everything from Phase 11 applies unchanged in production (see `docs/security-review.md`): safe
error JSON, no stack traces, security headers, restricted CORS, rate limiting, the 16 KB body cap,
the SQL validator, the read-only role, the statement timeout and the row limit.

**Two application limits, plus Google's.** The per-client limit (5 per 60 s) stops bursts from one
visitor; the global daily cap (`GLOBAL_DAILY_QUERY_LIMIT`) bounds total AI usage per day, which
IP rotation cannot get round. Its day resets at midnight Pacific time, like Gemini's
requests-per-day quota. It counts questions, and one question can use up to 4 Gemini requests
(3 SQL attempts and 1 insight), so 5 questions stay within 20 requests a day. Both limits live in
the process's memory: a restart resets them, and more instances would each count separately. They
are safety brakes, not a quota system; Google's quota is the hard limit.

Production status, as checked by the project owner in Google AI Studio and the Render dashboard:

- the production key is an Auth key, and the project is on the Free tier;
- Gemini 3.7 Flash limits: 5 requests per minute, 250K tokens per minute, 20 requests per day;
- Gemini 3.8 Flash allowed only 2 requests per day on this tier, too few for DataPilot, so Render
  sets `GEMINI_MODEL=gemini-3.7-flash`;
- Render sets `GLOBAL_DAILY_QUERY_LIMIT=5`.

Keep the key limited to the Gemini API where Google's console allows it. If the project is ever
billed, keep its quotas low and add a budget with alerts; budget alerts only notify, they do not
stop spending.

An IP-address restriction on the key is not recommended here: Render's outbound addresses are
shared by many services in the region rather than dedicated to this one, and can change, so it
would add little protection and could break production.

**Client IPs behind Render's proxy.** The rate limiter counts requests per client IP. On Render the
TCP peer (`request.client.host`) is a private `10.x` proxy address shared by every visitor, so it
cannot identify clients. Requests reach Render through Cloudflare, which puts the real visitor
address in `CF-Connecting-IP` and overwrites any value a client sends.

`backend/app/client_ip.py` therefore resolves the rate-limit identity as:

1. If `TRUST_CF_CONNECTING_IP=true` and `CF-Connecting-IP` holds one valid IP address, use it
   (IPv6 addresses are grouped by their /64 network, since one user usually controls a whole /64).
2. Otherwise use `request.client.host`.

`X-Forwarded-For` is never used, and uvicorn runs with `--no-proxy-headers`, because clients can
put anything at the start of that header. Only enable `TRUST_CF_CONNECTING_IP` where Cloudflare is
really in front (as on Render); anywhere else a client could set the header itself.
`--forwarded-allow-ips "*"` is not used; do not switch to it without a security review.
`--workers 1` keeps one process, so the in-memory limiter sees every request.

To verify after a deploy (no logging of IPs needed): send six blank questions from one network.
The sixth should return 429 `too_many_requests`, while a request from a different network (for
example a phone on mobile data) still gets 400 `invalid_question`. If both networks share one
limit, `CF-Connecting-IP` is not reaching the app; check `TRUST_CF_CONNECTING_IP` before treating
the rate limiter as production-verified.

## Reading the logs

Render's **Logs** tab shows one line per question from `app.request_log` (format and fields:
`docs/architecture.md`, "Request logs"). Every response has an `X-Request-ID` header, visible in
the browser's developer tools (Network tab); search the logs for that ID to find the request.

- `outcome=error` lines name the failure: `stage` and `cause` (for example `cause=gemini_timeout`,
  `cause=db_unavailable`), plus `error_kind`, the code the user saw.
- `cause=app_rate_limited` is DataPilot's own 5-per-minute limit and `cause=global_daily_limit` its
  daily cap; `cause=gemini_rate_limited` is Gemini's, with `limit_type` and `retry_after` when Gemini
  sent them.
- `gemini_sql_attempts=2` or `3` means Gemini returned 5xx or network errors and was retried.
- `insight_status=skipped_budget` means SQL generation and the database used most of the 45 s
  budget, so the optional insight was skipped.
- Unexpected errors log an ERROR line with the exception type, its request ID and a code-location
  traceback (no exception message).

Questions, SQL, rows and secrets are never logged. Uvicorn's access log still records each request
line with the TCP peer, which on Render is the internal proxy address, not the visitor's.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Frontend shows "Cannot connect to the DataPilot API" | Wrong `VITE_API_BASE_URL` (rebuild after fixing), the origin missing from `CORS_ALLOWED_ORIGINS`, or the service is asleep |
| First request after a while is very slow | Render's free plan sleeps when idle; the first request wakes it (the frontend waits up to 60 s) |
| Render fails at startup with a settings error | `CORS_ALLOWED_ORIGINS` is not a JSON list |
| Every question returns "The database is unavailable" | `READONLY_DATABASE_URL` is wrong, or uses the IPv6-only direct host instead of the pooler |
| Questions return 429 with an AI-service limit message | Gemini's rate limit or quota was reached (`cause=gemini_rate_limited` in the logs); the rest of the app keeps working |
| Questions return 503 "took too long" | Gemini did not answer within 20 s (`cause=gemini_timeout`) |
| Everyone shares one rate limit | `TRUST_CF_CONNECTING_IP` is not `true` on Render; see "Client IPs" above |
| Questions return 429 "daily AI request limit" | DataPilot's global daily cap was reached (`cause=global_daily_limit`); it reopens at midnight Pacific time or after a restart |
