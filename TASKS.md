# DataPilot — Implementation Roadmap (v1)

This is the step-by-step build plan for DataPilot v1. It is based strictly on [PROJECT.md](PROJECT.md), which remains the single source of truth for scope.

**How to use this file**

- Work through the phases in order. Each phase should work end to end before the next one starts.
- Tick tasks off as they are finished and commit with a clear message at the end of each phase.
- If a task requires a scope change, update PROJECT.md first.

---

## Phase 1 — Environment and project setup

- [x] Write the PROJECT.md specification
- [x] Initialize the git repository
- [x] Create the folder structure (`backend/`, `frontend/`, `database/`, `docs/`, `tests/`)
- [x] Add `.gitignore` (`.venv/`, `.env`, `__pycache__/`, `node_modules/`, `dist/`, `.DS_Store`)
- [x] Create the Python virtual environment (`.venv/`)
- [x] Create the local `.env` file (git-ignored)
- [x] Make the first commit
- [x] Create the GitHub repository and push

## Phase 2 — Minimal FastAPI backend scaffold

- [x] Add `backend/requirements.txt` with FastAPI and Uvicorn
- [x] Install backend dependencies into `.venv`
- [x] Create the FastAPI application entry point in `backend/`
- [x] Add a settings module that reads configuration from environment variables
- [x] Add a `GET /health` endpoint that returns a simple status response
- [x] Run the app locally with Uvicorn and check `/health` and the auto-generated `/docs`
- [x] Commit: backend scaffold

## Phase 3 — PostgreSQL schema

- [x] Create the Supabase project to use as the PostgreSQL database
- [x] Store the database connection string in `.env`
- [x] Write `database/schema.sql` with the six tables: `customers`, `categories`, `products`, `orders`, `order_items`, `payments`
- [x] Add primary keys, foreign keys and sensible constraints (e.g. non-negative prices and quantities)
- [x] Use clear, self-explanatory column names
- [x] Define the order status values so that `delivered` is the Completed Order status (plus e.g. `cancelled` and `returned`)
- [x] Define the payment status values so that `completed` is the Successful Payment status (plus e.g. `failed`, `pending` and `refunded`)
- [x] Apply the schema to Supabase and check that all six tables and relationships exist
- [x] Commit: database schema

## Phase 4 — Faker seed data

- [x] Add Faker and a PostgreSQL driver to the requirements
- [x] Create `database/seed.py` with a fixed random seed so the dataset is reproducible
- [x] Make the seed script safe to re-run (clear existing rows before inserting)
- [x] Generate categories (e.g. Electronics, Apparel, Home)
- [x] Generate 100–200 products with a price, a cost lower than the price, and stock
- [x] Generate about 1,000 customers with name, email, city, state/country and signup date
- [x] Generate a few thousand orders with dates spread over about two years and a mix of statuses
- [x] Generate order items with product, quantity, `unit_price` and `unit_cost` (both captured at the time of the order)
- [x] Generate payments with varied methods and statuses that are consistent with each order's status
- [x] Run the seed script against Supabase
- [x] Check row counts and run hand-written SQL for Revenue, Profit and AOV to confirm the data looks realistic
- [x] Commit: seed data

## Phase 5 — SQL validator and tests

- [x] Add SQLGlot and pytest to the requirements
- [x] Create the validator as a pure function: SQL string in → approved (possibly rewritten) SQL or a rejection reason out
- [x] Rule 1: reject more than one statement
- [x] Rule 2: allow only `SELECT` (including `WITH ... SELECT`); reject all write/DDL statements, including inside CTEs
- [x] Rule 3: allow only the six known tables; reject `pg_catalog`, `information_schema` and other schemas
- [x] Rule 4: reject dangerous functions (e.g. `pg_sleep`, `pg_read_file`, `lo_import`, `dblink`)
- [x] Rule 5: add a `LIMIT` if missing, and cap it if it is too large
- [x] Rule 6: fail closed when SQL cannot be parsed or confirmed safe
- [x] Write tests in `tests/` for allowed queries (simple select, joins, aggregates, CTEs)
- [x] Write tests in `tests/` for blocked queries (one or more per rule)
- [x] All validator tests pass
- [x] Commit: SQL validator and tests

## Phase 6 — Database executor and connection

- [x] Write SQL in `database/` that creates a read-only role with `SELECT` on the six tables only
- [x] Apply it to Supabase and confirm that writes fail when connected as that role
- [x] Store the read-only connection string in `.env`
- [x] Create the DB executor module that connects as the read-only role
- [x] Run every query inside a read-only transaction
- [x] Set a short statement timeout on each query
- [x] Enforce a maximum number of returned rows
- [x] Return column names and rows in a JSON-friendly form (dates, decimals)
- [x] Manually run a few approved queries through the executor
- [x] Commit: DB executor

## Phase 7 — Gemini NL-to-SQL integration

- [x] Get a Gemini API key and store it in `.env`
- [x] Add the Gemini SDK to the requirements
- [x] Create the LLM service module (the only module that talks to Gemini)
- [x] Write the schema context: six tables, columns and relationships
- [x] Add the Business Metric Definitions (Completed Order, Successful Payment, Revenue, Profit, AOV) to the prompt
- [x] Write the NL → SQL prompt: PostgreSQL `SELECT` only, with the user's question treated as data
- [x] Extract clean SQL from the model response (e.g. strip markdown code fences)
- [x] Try a handful of example questions and check that the generated SQL looks correct
- [x] Commit: Gemini NL → SQL service

## Phase 8 — Query pipeline and API endpoint

- [x] Create the query pipeline: generate → validate → execute
- [x] Add a Pydantic request model with a maximum question length
- [x] Add a Pydantic response model (generated SQL, columns, rows)
- [x] Add the query endpoint to FastAPI that calls the pipeline
- [x] Return a clear error when the validator rejects SQL
- [x] Test the endpoint end to end from `/docs`
- [x] Commit: query pipeline and endpoint

## Phase 9 — Minimal React frontend

- [x] Create the React + Vite app in `frontend/`
- [x] Read the backend URL from a frontend environment variable
- [x] Allow the local frontend origin in the backend CORS settings
- [x] Add the question input and submit button
- [x] Call the backend and show a loading state
- [x] Show the generated SQL
- [x] Show the results table
- [x] Add clickable example questions
- [x] Show error messages returned by the backend
- [x] Commit: minimal frontend

## Phase 10 — Charts and business insight

- [x] Install Recharts
- [x] Implement the deterministic chart selection rules in application code (not the LLM):
  - [x] Single aggregate value → KPI
  - [x] Category + numeric value → bar chart
  - [x] Date/time + numeric value → line chart
  - [x] Otherwise → table only
- [x] Add the KPI display
- [x] Add the bar chart
- [x] Add the line chart
- [x] Add the insight prompt to the LLM service, based on the question and the returned rows
- [x] Add the summarize step to the pipeline and include the insight in the API response
- [x] Add the insight panel to the frontend
- [x] Commit: charts and insight

## Phase 11 — Error handling and security hardening

- [x] Friendly errors for: invalid question, rejected SQL, query timeout, no results
- [x] Add a global error handler so stack traces, credentials and internal details are never returned
- [x] Restrict CORS to the deployed frontend origin plus localhost (from configuration)
- [x] Add basic per-client rate limiting
- [x] Confirm the question length limit and Pydantic validation on all request bodies
- [x] Try prompt-injection style questions and confirm the validator and read-only role still block unsafe SQL
- [x] Confirm that no secrets are committed to the repository
- [x] Review all 15 Security Requirements in PROJECT.md and tick each one off
- [x] Commit: error handling and security hardening

## Phase 12 — Testing and benchmark questions

- [x] Write a benchmark list of example business questions covering rankings, totals, trends and comparisons
- [x] Include questions that use each business metric (Revenue, Profit, AOV, Completed Order, Successful Payment)
- [x] Write the expected answer for each benchmark question using hand-written SQL
- [ ] Run every benchmark question through the app and record pass/fail
- [ ] Improve the schema context or prompt for the failing questions and re-run
- [x] Check that each chart rule is triggered by at least one benchmark question
- [x] Manually test each error case (bad question, rejected SQL, timeout, empty results)
- [x] Confirm that the validator tests cover both allowed and blocked cases and all pass
- [x] Commit: benchmark questions and results

## Phase 13 — Deployment

- [x] Confirm that the Supabase database has the schema, seed data and read-only role
- [x] Deploy the backend to Render
- [x] Set backend environment variables in Render (Gemini key, read-only database URL, allowed frontend origin)
- [x] Check `/health` on the Render URL
- [x] Deploy the frontend to Vercel with the backend URL configured
- [x] Set the backend CORS origin to the Vercel URL
- [x] Test the full flow on the public URL
- [x] Confirm that the frontend bundle contains no secrets
- [x] Commit: deployment configuration

## Phase 14 — Final UI polish

- [x] Clean layout and consistent styling across all panels
- [x] Polished loading states
- [x] Polished error and empty-result states
- [x] Readable SQL viewer
- [x] Responsive design (desktop and mobile widths)
- [x] Redeploy and check the live site
- [x] Commit: UI polish

## Phase 15 — README, diagrams, demo and resume presentation

- [x] Write the README: what DataPilot is, how it works, how to run it locally
- [x] Add screenshots or a demo to the README
- [x] Create the architecture diagram in `docs/`
- [x] Document the safety model (validator rules + read-only database layer) in `docs/`
- [x] Add the benchmark results to `docs/`
- [x] Record a short demo video or GIF
- [x] Write resume bullet points and a short project summary
- [ ] Practise explaining the architecture, safety model and design decisions without notes
- [ ] Final check against the Definition of a Successful Final Product (PROJECT.md §12)

---

## Nice to have (only after all phases above are complete)

From PROJECT.md §8. Do not start these until v1 is finished.

- [ ] Session-level query history in the UI
- [ ] Schema browser panel showing the tables and columns
- [ ] Copy SQL / export results as CSV
- [ ] Manual chart-type toggle
- [ ] Light/dark theme
