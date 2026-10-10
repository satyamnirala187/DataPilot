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

The browser only ever talks to the Render API. Secrets (Gemini key, database URLs) live only in
Render's environment; the frontend holds just the public API URL. The API reaches Supabase as two
roles: `datapilot_readonly` for questions and, for History and Saved Reports, `datapilot_app`.

## Prerequisites

- The GitHub repository with this code pushed to `main`.
- The Supabase database already has the schema, seed data and the `datapilot_readonly` role
  (`database/`). It is reused as is; nothing is recreated or reseeded.
- The read-only connection string from Supabase, using the **session pooler** (IPv4). Render cannot
  reach Supabase's IPv6-only direct host (`db.<ref>.supabase.co`).
- A Gemini API key.
- For History and Saved Reports: schema `datapilot` and the role `datapilot_app`, created once with
  `database/create_app_role.py`, which also wrote `APP_DATABASE_URL` to the local `.env`.

## 1. Backend on Render

Create the service from the Blueprint (`render.yaml`, **New → Blueprint**), or by hand
(**New → Web Service**) with the same values:

| Setting | Value |
|---|---|
| Runtime | Python 3 |
| Root directory | `backend` |
| Build command | `pip install -r requirements.txt` (runtime packages only; pytest and Faker are in `requirements-dev.txt`) |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 --no-proxy-headers` |
| Health check path | `/health` |
| Instance | one instance (the free plan is fine) |
| Python version | environment variable `PYTHON_VERSION=3.13.16` (not a secret) |
| Client IPs | environment variable `TRUST_CF_CONNECTING_IP=true` (not a secret, see [Client IPs](#security-notes)) |

Environment variables (names only; enter values in the Render dashboard):

| Name | Required | Value |
|---|---|---|
| `READONLY_DATABASE_URL` | yes | read-only role via the session pooler (a secret). The backend requires TLS whatever the URL says, so `?sslmode=require` is optional |
| `APP_DATABASE_URL` | for History | the `datapilot_app` role via the session pooler (a secret), from the local `.env`. Without it questions still work, but no History is stored and the History endpoints return 503; see [History and Saved Reports](#history-and-saved-reports) |
| `GEMINI_API_KEY` | yes | Gemini API key |
| `CORS_ALLOWED_ORIGINS` | yes | JSON list, see [CORS](#cors) |
| `DEMO_USERNAME` | yes | the shared demo login name (a secret); see [Private demo access](#private-demo-access) |
| `DEMO_PASSWORD` | yes | the shared demo password, at least 16 characters (a secret) |
| `DEMO_SESSION_SECRET` | yes | signs session tokens, at least 32 characters (a secret, never shared) |
| `DEMO_SESSION_MINUTES` | no | session length, default 120; leave unset |
| `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS` | no | default 5 questions per 60 s per client |
| `MAX_RESULT_BYTES` | no | default 1,000,000; larger answers are refused with 422 `result_too_large` |
| `GLOBAL_DAILY_QUERY_LIMIT` | no | default 5 questions per Pacific day from all clients; set to `5` on Render. Size it to the Gemini quota: one question can use up to 4 Gemini requests |
| `GEMINI_MODEL` | no | default `gemini-3.6-flash`; set explicitly on Render |
| `ENABLE_API_DOCS` | no | leave unset in production: `/docs`, `/redoc` and `/openapi.json` then return 404 |

Never set the admin `DATABASE_URL` on Render: it is only for the setup scripts in `database/`.
`APP_DATABASE_URL` is a different, much narrower connection, not a replacement for it.

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
2. Deploy the backend on Render, with the `DEMO_*` secrets set and `CORS_ALLOWED_ORIGINS` set to
   the localhost origins for now.
3. Check `/health` on the Render URL.
4. Copy the Render URL.
5. Set `VITE_API_BASE_URL` to it in Vercel.
6. Deploy the frontend on Vercel.
7. Copy the Vercel production URL.
8. Add it to `CORS_ALLOWED_ORIGINS` on Render.
9. Save; Render restarts the service with the new value.
10. Log in on the Vercel URL and ask a question: frontend → Render → Supabase (and Gemini).

## Private demo access

The hosted demo is behind one shared login, to protect the limited Gemini quota. It is a gate, not
an account system: no sign-up, no user database, no cookies. The browser logs in with
`POST /auth/login`, keeps the returned 2-hour token in `sessionStorage` and sends it as
`Authorization: Bearer <token>`; the backend refuses every `POST /query` without a valid one, before
any rate limit, Gemini call or database query. `/health` stays public. Details:
`docs/safety-model.md`, "Request-level protection".

### Setting the secrets

Generate values **on your own machine** and paste them straight into Render (service →
**Environment**). Never put them in Git, the README, screenshots or chat (including AI assistants).

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(18))"   # DEMO_PASSWORD (24 characters)
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # DEMO_SESSION_SECRET (64 characters)
```

`DEMO_USERNAME` can be any short name (1–100 characters). Keep the username and password somewhere
private, such as a password manager, to share with people you invite. `DEMO_SESSION_SECRET` is
never shared with anyone. The backend refuses to start if `DEMO_PASSWORD` is under 16 characters or
`DEMO_SESSION_SECRET` under 32, and the error never shows the value.

### Rolling out the login gate

The backend and frontend changes go out together:

1. **Add the three secrets in Render first.** The version running before the gate ignores unknown
   `DEMO_*` settings, so this is harmless; saving may redeploy the current version.
2. **Check they exist** in the Environment list, without revealing their values.
3. **Push** the backend and frontend commits together.
4. **Wait for both deploys.** Vercel usually finishes before Render, so for a few minutes the old
   and new versions may be paired. Neither pairing opens `/query`: the new backend refuses it without
   a session, and the new frontend cannot get past the login page until the new backend is live.
5. **Verify without spending Gemini quota** (below).

### Verifying without Gemini

Use the browser for anything that needs the password; the `curl` checks need no credentials.

1. `curl -s https://<service>.onrender.com/health` returns `{"status":"ok",...}`.
2. The Vercel URL shows **Private Demo Access**, not the question box.
3. A wrong username or password shows "Incorrect username or password." and no workspace. Each try
   counts towards the limit of 5 logins per 15 minutes per network.
4. The right credentials open the workspace.
5. Reloading the tab returns to the workspace without logging in again (the stored session is
   checked with `GET /auth/session`, which must return 200 in the Network tab).
6. A question sent without a token is refused before Gemini:
   `curl -s -X POST https://<service>.onrender.com/query -H 'Content-Type: application/json' -d '{"question":"test"}'`
   returns 401 `unauthorized`, and the Render log line shows `stage=auth cause=missing_token
   gemini_sql_attempts=0`.
7. **Log out** returns to the login page. To confirm the server revoked the token, run in the
   browser console *before* logging out `const t = sessionStorage.getItem('datapilot_demo_token')`
   (it prints nothing), log out, then run
   `fetch('https://<service>.onrender.com/auth/session', {headers: {Authorization: 'Bearer ' + t}}).then(r => console.log(r.status))`;
   it prints `401`.
8. `curl -s -o /dev/null -w '%{http_code}' https://<service>.onrender.com/docs` prints `404`.
9. Steps 3–7 working from the Vercel site confirms CORS allows the `Authorization` header.

One real question can then be asked, if you choose to spend Gemini quota on it.

### Rotating access

- **Change who can log in:** set a new `DEMO_PASSWORD` (and `DEMO_USERNAME`, if wanted). Existing
  sessions keep working until they expire, at most 2 hours.
- **Log everyone out now:** set a new `DEMO_SESSION_SECRET`. Every outstanding token stops working.

Render restarts the service when a variable is saved. Logout revocations live in memory, so a
restart forgets them: a logged-out token could then work again until its 2-hour expiry, which
rotating `DEMO_SESSION_SECRET` also ends.

### If the rollout goes wrong

Never remove the gate by making `/query` public again; there is deliberately no switch to turn it
off. Either fix forward, or revert the backend and frontend login commits **together** and push, so
the previous version (with its rate limit and daily cap) returns as a whole. If login says
"Demo access is temporarily unavailable", a `DEMO_*` secret is missing on Render.

## History and Saved Reports

Phase 19 stores every successful answer as History and lets users save one as a report
(`docs/architecture.md`, "Application data"). In production this needs one more secret:

**`APP_DATABASE_URL`** is the connection for the role `datapilot_app`. That role can only read and
add rows in `datapilot.analyses` and `datapilot.saved_reports`: no access to the business tables, no
updates or deletes. It is never used for generated SQL, which keeps running as
`datapilot_readonly`. `database/create_app_role.py` wrote it to the local `.env`, with the same
session-pooler host as `READONLY_DATABASE_URL` and `sslmode=require`.

It is **not** the admin `DATABASE_URL`. Never put the admin URL on Render, even to make History work.

### Setting `APP_DATABASE_URL`

1. Open the local `.env` in an editor and copy the value of `APP_DATABASE_URL` (everything after
   the first `=`). Do not print it in a terminal, and never paste it into Git, docs, screenshots or
   chat (including AI assistants).
2. In Render, open the service → **Environment** → add `APP_DATABASE_URL` as a secret and paste the
   value. The Blueprint already declares the key with `sync: false`, so it has no value in Git.
3. Check it appears in the Environment list, without revealing it.

Re-running `create_app_role.py` rotates the role's password and rewrites the local value; copy the
new value to Render straight away, or History stops being stored (and the endpoints return 503)
until you do.

### Rolling out

1. **Set `APP_DATABASE_URL` on Render first.** Saving restarts the version already running, which
   ignores settings it does not know, so this is harmless.
2. **Then push** the Phase 19 commits. Render deploys the new backend, which picks up
   `APP_DATABASE_URL` at startup.
3. **The frontend needs no change**: no code and no environment variable. Vercel may rebuild the
   same frontend on push; that is harmless. The current frontend ignores the new `analysis_id` field,
   and there is no History or Saved Reports screen yet.
4. History starts with the **first successful question answered by the new backend**. Earlier
   answers were never stored and are not created retroactively.

### Production verification plan

Not run yet; to be done after the rollout. Spend Gemini quota on one question only.

**A. Without Gemini or credentials** (`curl` from any machine):

```bash
API=https://<service>.onrender.com
curl -s "$API/health"                                            # 200 {"status":"ok",...}
curl -s -o /dev/null -w '%{http_code}\n' "$API/history"          # 401
curl -s -o /dev/null -w '%{http_code}\n' "$API/saved-reports"    # 401
curl -s -o /dev/null -w '%{http_code}\n' "$API/docs"             # 404
curl -s -o /dev/null -w '%{http_code}\n' "$API/openapi.json"     # 404
```

The two 401s come with `{"error":{"code":"unauthorized",...}}`: the endpoints exist and refuse
requests without a session before touching the database.

**B. One real question** (uses Gemini quota once):

1. Log in on the production Vercel site as usual.
2. Ask exactly **one** simple question, for example "What is the total revenue from delivered
   orders?". Do not run the benchmark or any other Gemini tests.
3. The answer appears as before.
4. In the Network tab, the `/query` response has a non-null `analysis_id`.
5. Render's log line for it (search for its `X-Request-ID`) shows `outcome=success` and
   `history_status=saved`. `history_status=disabled` means `APP_DATABASE_URL` is missing;
   `failed` (with `history_error`) means it is set but the database refused it.

**C. History and Saved Reports, with no further Gemini calls**, from the browser console on the
Vercel site while logged in (the token never leaves the browser and is never printed):

```js
const api = 'https://<service>.onrender.com'
const auth = {Authorization: 'Bearer ' + sessionStorage.getItem('datapilot_demo_token')}
const history = await (await fetch(`${api}/history`, {headers: auth})).json()
console.log(history.items.length, history.items[0]?.question)                 // 1, the question asked
const id = history.items[0].id
console.log((await (await fetch(`${api}/history/${id}`, {headers: auth})).json()).rows.length)
```

The detail shows the same rows, SQL, chart and insight as the answer in step B. Optionally, save it:

```js
const save = () => fetch(`${api}/saved-reports`, {method: 'POST', headers: {...auth, 'Content-Type': 'application/json'},
                                                    body: JSON.stringify({analysis_id: id, title: 'Total revenue'})})
console.log((await save()).status, (await save()).status)                      // 201 409
const reports = await (await fetch(`${api}/saved-reports`, {headers: auth})).json()
console.log(reports.items.length, (await fetch(`${api}/saved-reports/${reports.items[0].id}`, {headers: auth})).status)  // 1 200
```

A saved report is permanent: V1 has no delete, so choose a title worth keeping, or skip the save.
None of these requests calls Gemini, runs SQL on the business tables or uses the daily cap.

### Rolling back

- **Turn History off:** remove `APP_DATABASE_URL` on Render (Render restarts the service). Questions
  keep working exactly as before and return `analysis_id: null`; the log shows
  `history_status=disabled`. The History and Saved Reports endpoints return 503
  `history_unavailable`. Answering business questions (Gemini, the validator, the read-only role)
  does not depend on History in any way.
- Stored analyses and saved reports stay in Supabase and are served again once the variable is back.
- To go back to the previous code entirely, revert the Phase 19 commits and push; the database
  schema and role can stay, unused.

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
- Render sets `GEMINI_MODEL=gemini-3.6-flash`, the backend's default too;
- Gemini 3.6 Flash limits: 5 requests per minute, 250K tokens per minute, 20 requests per day;
- Render sets `GLOBAL_DAILY_QUERY_LIMIT=5`: 5 questions × up to 4 Gemini requests = 20 a day.

Model history: Gemini 3.8 Flash allowed only 2 requests per day on this tier, too few for
DataPilot. Gemini 3.7 Flash was used next, until Google deprecated it (its requests are redirected
to 3.8 Flash); the project owner then switched Render to 3.6 Flash.

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
- `stage=auth` lines are questions refused for want of a valid session (`cause=missing_token`,
  `invalid_token`, `expired_token`, `revoked_token` or `not_configured`); nothing else ran.
- `event=login` lines record each login attempt: `outcome=success`, `failure`, `rate_limited`,
  `unavailable` or `invalid_request`. The username and password are never logged.
- `insight_status=skipped_budget` means SQL generation and the database used most of the 45 s
  budget, so the optional insight was skipped.
- `history_status=saved` means the answer was stored as History; `disabled` means
  `APP_DATABASE_URL` is not set; `failed` (a WARNING line, with `history_error`) means storing failed
  while the user still got the answer.
- `event=history_unavailable` lines are History or Saved Reports requests that got 503, with the
  endpoint and a fixed `cause` (`disabled`, `unavailable`, `timeout`, ...).
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
| Login says "Demo access is temporarily unavailable" | `DEMO_USERNAME`, `DEMO_PASSWORD` or `DEMO_SESSION_SECRET` is not set on Render (`outcome=unavailable` in the logs); every question is refused meanwhile |
| Render fails at startup after setting a `DEMO_*` secret | `DEMO_PASSWORD` is under 16 characters or `DEMO_SESSION_SECRET` under 32 |
| Everyone is sent back to the login page | `DEMO_SESSION_SECRET` was changed, or sessions reached their 2-hour expiry |
| Everyone shares one rate limit | `TRUST_CF_CONNECTING_IP` is not `true` on Render; see "Client IPs" above |
| Questions return 429 "daily AI request limit" | DataPilot's global daily cap was reached (`cause=global_daily_limit`); it reopens at midnight Pacific time or after a restart |
| Answers have `analysis_id: null`; History endpoints return 503 | `APP_DATABASE_URL` is missing on Render (`history_status=disabled`, `cause=disabled`), or wrong or out of date after `create_app_role.py` was re-run (`history_error=unavailable`) |
