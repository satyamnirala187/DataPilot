# DataPilot — Project Blueprint

> An AI-powered business data analyst for a fictional e-commerce company.
> Ask a business question in plain English, get back a safe SQL query, the results, a chart, and a short business insight.

This document is the single source of truth for what DataPilot is, what it is not, and how it is built. Any change in scope should be reflected here first.

---

## 1. Project Overview

DataPilot lets a non-technical user ask questions such as *"What were our top 5 product categories by revenue last quarter?"* and get an answer without writing SQL.

Behind the scenes, a large language model (Gemini) translates the question into a PostgreSQL `SELECT` query. Before it runs, the query is checked by a validation layer (SQLGlot) so that only safe, read-only SQL ever reaches the database. The results are returned to a React frontend, which shows them as a table and, where it makes sense, a chart. The AI then writes a short, plain-language insight about what the data shows.

The data belongs to a **fictional e-commerce store** and is generated synthetically with Faker, so the project contains no real customer data.

**Who it is for**

| Audience | What DataPilot should show them |
|---|---|
| Recruiters / interviewers | Full-stack ability, practical LLM integration, security awareness, clean architecture |
| Academic evaluators | A clear problem, a well-defined scope, a working system, and an explainable design |
| A business user (the persona the product is designed for) | Fast answers to data questions without needing SQL or an analyst |

---

## 2. Problem Statement

Business data usually lives in relational databases, and getting answers out of it needs SQL. Most business users (managers, marketers, operations staff) cannot write SQL, so they either:

- wait for a data analyst to answer each question, which is slow and creates a bottleneck, or
- rely on fixed dashboards that only answer the questions someone anticipated in advance.

LLMs can now translate natural language into SQL fairly reliably, but letting an AI run queries on a database directly is risky. Generated SQL can be wrong, slow, or destructive (`DELETE`, `DROP`, etc.).

**DataPilot addresses this by combining natural-language-to-SQL generation with a strict safety layer**, so a user can explore the data conversationally while the database stays protected.

---

## 3. Project Objectives

1. **Natural-language querying.** Turn plain-English business questions into correct PostgreSQL queries against a known e-commerce schema.
2. **Safety by design.** Make sure only read-only SQL can ever run, enforced in several independent layers rather than trusting the LLM.
3. **Useful output.** Show results as a clean table, choose a suitable chart where appropriate, and add a short AI-written business insight.
4. **Transparency.** Always show the user the generated SQL so they can see exactly what was run.
5. **Clean, explainable architecture.** Keep a clear separation between the frontend, API, AI service, validation and database layers, so each part can be explained on its own.
6. **Professional presentation.** Deliver a polished, SaaS-style interface and deploy it publicly so it can be demonstrated live.
7. **Realistic data.** Provide a believable synthetic dataset that is large and varied enough to produce interesting answers (trends, rankings, comparisons).

---

## 4. Core User Flow

```
 1. User types a business question
        │
        ▼
 2. Backend sends the question + database schema context to Gemini
        │
        ▼
 3. Gemini returns a PostgreSQL SELECT query
        │
        ▼
 4. SQL is validated for safety (SQLGlot + rule checks)
        │            └── rejected → user sees a clear, friendly error
        ▼
 5. Query executes against PostgreSQL (read-only role, timeout, row limit)
        │
        ▼
 6. Results are returned to the frontend
        │
        ▼
 7. Frontend displays the generated SQL, a results table and a chart (when suitable)
        │
        ▼
 8. AI provides a short business insight based on the results
```

**Example**

- **Question:** "Which 5 cities generated the most revenue in 2025?"
- **Generated SQL:** a `SELECT` that joins `customers` → `orders` → `order_items`, keeps only completed (`delivered`) orders, sums `order_items.quantity * order_items.unit_price` as revenue, groups by city, orders by revenue and applies `LIMIT 5`
- **Output:** a 5-row table and a bar chart
- **Insight:** "Mumbai and Delhi together account for nearly 40% of revenue among the top 5 cities, with Mumbai leading by a clear margin."

---

## 5. Technology Stack

| Layer | Technology | Why it was chosen |
|---|---|---|
| IDE | Cursor | AI-assisted editor for day-to-day development |
| Development agent | Claude Code | Helps with planning, implementation and review |
| Backend language | Python | Strong ecosystem for data and AI work; easy to read and explain |
| API framework | FastAPI | Fast, modern, typed (Pydantic), automatic OpenAPI docs |
| Database | PostgreSQL | Industry-standard relational database |
| Database hosting | Supabase (managed PostgreSQL) | Free tier, hosted Postgres with no infrastructure to manage |
| LLM | Gemini API | Turns natural language into SQL and generates insights |
| SQL validation | SQLGlot | Parses SQL into a syntax tree so safety checks are structural, not string matching |
| Synthetic data | Faker | Generates realistic customers, products and orders |
| Frontend | React + Vite | Standard modern frontend stack with fast dev builds |
| Charts | Recharts | Simple, declarative React charting |
| Frontend hosting | Vercel | Simple deployment for Vite/React apps |
| Backend hosting | Render | Simple deployment for Python web services |
| Version control | Git + GitHub | Source control and public portfolio visibility |

---

## 6. High-Level Architecture

```
┌──────────────────────────┐
│   React + Vite Frontend  │   (Vercel)
│  - Question input        │
│  - SQL viewer            │
│  - Results table         │
│  - Recharts chart        │
│  - Insight panel         │
└────────────┬─────────────┘
             │  HTTPS / JSON
             ▼
┌──────────────────────────────────────────────────────┐
│                  FastAPI Backend                     │   (Render)
│                                                      │
│  API layer  ──►  Query pipeline (orchestrates steps) │
│                     │                                │
│     ┌───────────────┼──────────────────┐             │
│     ▼               ▼                  ▼             │
│  LLM service    SQL validator     DB executor        │
│  (Gemini)       (SQLGlot)         (read-only conn)   │
│  - NL → SQL     - SELECT only     - timeout          │
│  - insight      - table allowlist - row limit        │
│                 - LIMIT enforced                     │
└──────┬─────────────────────────────────┬─────────────┘
       │                                 │
       ▼                                 ▼
┌──────────────┐               ┌────────────────────────┐
│  Gemini API  │               │  PostgreSQL (Supabase) │
└──────────────┘               │  e-commerce schema     │
                               └────────────────────────┘
```

**Responsibilities**

- **Frontend:** collects the question, calls the API, renders the SQL, table, chart and insight. It holds no secrets and talks to nothing except the backend.
- **API layer:** HTTP endpoints, request/response validation (Pydantic), error handling.
- **Query pipeline:** runs the steps in order: generate → validate → execute → summarize. This is the core of the application.
- **LLM service:** the only module that talks to Gemini. It holds the prompts and the schema context.
- **SQL validator:** a pure function that takes a SQL string and either approves it (possibly rewritten, e.g. with a `LIMIT` added) or rejects it with a reason. It has no network or database access, so it is easy to unit test.
- **DB executor:** runs approved SQL through a read-only database connection, with a timeout and a row cap.

**Planned repository layout** (folders already exist)

```
DataPilot/
├── backend/     FastAPI app, pipeline, services, validator
├── frontend/    React + Vite app
├── database/    schema SQL and Faker seed scripts
├── docs/        diagrams, design notes, evaluation material
└── tests/       backend tests (validator tests first)
```

---

## 7. Database Scope

Version 1 uses **one PostgreSQL database** with **six tables** that model a simple online store. All data is synthetic, generated with Faker using a fixed random seed so the dataset can be reproduced.

| Table | Purpose | Key relationships |
|---|---|---|
| `customers` | People who shop at the store (name, email, city, state/country, signup date) | One customer → many orders |
| `categories` | Product categories (e.g. Electronics, Apparel, Home) | One category → many products |
| `products` | Items for sale (name, category, current price, current cost, stock) | Many products → one category |
| `orders` | A purchase by a customer (date, status) | Many orders → one customer |
| `order_items` | Line items in an order (product, quantity, and the unit price and unit cost at the time of the order) | Many items → one order; many items → one product |
| `payments` | Payment records for orders (amount, method, status, date) | Many payments → one order |

```
customers ──< orders ──< order_items >── products >── categories
                 │
                 └──< payments
```

**Data guidelines**

- Enough volume to give meaningful aggregates. As a rough target: around 1,000 customers, 100–200 products, a few thousand orders, and order dates spread over about two years so trends and seasonality show up.
- Realistic variety: different order statuses (e.g. delivered, cancelled, returned), payment methods and price ranges.
- Primary keys, foreign keys and sensible constraints, so the schema itself is well designed.
- Column names should be clear and self-explanatory, which also helps the LLM write correct SQL.

The exact column definitions will be written in `database/` when the schema is implemented.

### Business Metric Definitions

These rules give the LLM consistent business semantics. They are included in the prompt alongside the schema so that the same question always maps to the same calculation.

| Term | Definition |
|---|---|
| **Completed Order** | An order whose `orders.status` is `delivered`. Cancelled and returned orders are not completed. |
| **Successful Payment** | A payment whose `payments.status` is `completed`. Failed, pending or refunded payments are not successful. |
| **Revenue** | The sum of `order_items.quantity × order_items.unit_price` across **completed orders** only. |
| **Profit** | Revenue minus the cost captured at the time of the order: the sum of `order_items.quantity × (order_items.unit_price − order_items.unit_cost)` across **completed orders** only. `products.cost` is the current cost and is not used for historical profit. |
| **Average Order Value (AOV)** | Revenue divided by the number of distinct **completed orders**. |

The exact status values must match these definitions when the schema is written in `database/`.

---

## 8. Initial Feature Scope (Version 1)

### Must have

- [ ] Natural-language question input
- [ ] Gemini-based NL → PostgreSQL SQL generation using the schema as context
- [ ] SQLGlot-based safety validation (see Security Requirements)
- [ ] Safe query execution against PostgreSQL
- [ ] Generated SQL shown to the user
- [ ] Results table
- [ ] Automatic chart for suitable results, chosen by deterministic application rules (see below), with a table-only fallback
- [ ] Short AI-generated business insight based on the returned rows
- [ ] Clear, friendly errors (invalid question, rejected SQL, query timeout, no results)
- [ ] Clickable example questions to help new users get started
- [ ] Health-check endpoint for the backend
- [ ] Synthetic dataset generation script
- [ ] Unit tests for the SQL validator
- [ ] Deployed frontend (Vercel) and backend (Render)

**Chart selection rules**

The chart type is chosen by fixed rules in application code based on the shape of the result. The LLM does not choose the chart.

| Result shape | Display |
|---|---|
| A single aggregate value (one row, one numeric column) | KPI |
| A category column + a numeric column | Bar chart |
| A date/time column + a numeric column | Line chart |
| Anything else | Table only |

### Nice to have (only after the must-haves work)

- [ ] Session-level query history in the UI
- [ ] Schema browser panel showing the tables and columns
- [ ] Copy SQL / export results as CSV
- [ ] Manual chart-type toggle
- [ ] Light/dark theme

---

## 9. Security Requirements

**Core rule: AI-generated SQL is untrusted input.** The LLM's output is never trusted. Safety is enforced in code and at the database level, not just in the prompt.

### SQL validation (application layer, SQLGlot)

1. **Single statement only.** Reject input that contains more than one statement (e.g. `SELECT ...; DROP TABLE ...`).
2. **Read-only only.** The parsed statement must be a `SELECT` (including `WITH ... SELECT` CTEs). Reject `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `GRANT`, `REVOKE`, `COPY` and any other non-query statement, including anything nested inside a CTE.
3. **Table allowlist.** Only the six known tables can be referenced. Reject system catalogs and other schemas (e.g. `pg_catalog`, `information_schema`).
4. **Dangerous function blocklist.** Reject functions that can read files, sleep, or reach outside the database (e.g. `pg_sleep`, `pg_read_file`, `lo_import`, `dblink`).
5. **Row limit.** Enforce a maximum number of returned rows by adding or capping a `LIMIT`.
6. **Fail closed.** If the SQL cannot be parsed or its safety cannot be confirmed, reject it.

### Database layer (defense in depth)

7. **Read-only database role.** The backend connects as a dedicated PostgreSQL user that only has `SELECT` on the six tables. Even if validation were bypassed, writes would fail.
8. **Read-only transactions.** Queries run inside a read-only transaction.
9. **Statement timeout.** Each query has a short timeout so expensive queries cannot hang the system.

### Application and deployment

10. **Secrets stay server-side.** The Gemini API key and database credentials live in environment variables (`.env` locally, Render settings in production). They are never committed and never sent to the frontend.
11. **Restricted CORS.** The backend only accepts browser requests from the deployed frontend origin (plus localhost during development).
12. **Input limits.** Limit the length of the question and validate all request bodies with Pydantic.
13. **Safe error messages.** Users see friendly messages; stack traces, credentials and internal details are never returned.
14. **Basic rate limiting.** Limit requests per client to protect the Gemini quota and the database.
15. **Prompt-injection awareness.** The user's question is treated as data inside the prompt. Even if a user manipulates the LLM, rules 1–9 still apply.

---

## 10. Project Boundaries — Intentionally Excluded

These items are **out of scope for version 1**. Leaving them out is deliberate: it keeps the project focused, finishable and easy to explain.

| Excluded | Reason |
|---|---|
| Kubernetes, Docker orchestration | Unnecessary for a single small service; Render handles deployment |
| Kafka or any message queue | No asynchronous or streaming workload |
| Vector databases / RAG | The schema is small and fixed and fits directly in the prompt |
| LangChain or similar agent frameworks | Direct API calls are simpler, more transparent and easier to explain |
| Multi-agent systems | One clear pipeline is enough |
| AWS / GCP / Azure infrastructure | Supabase + Render + Vercel cover all hosting needs |
| Write operations of any kind | DataPilot is strictly an analysis tool |
| Multiple databases or user-uploaded databases | Version 1 targets one known e-commerce schema |
| User accounts / authentication | Not needed for a public demo on synthetic data |
| Real customer or personal data | All data is synthetic |
| Fine-tuning or training models | Prompting a hosted model is sufficient |
| Conversational follow-ups / chat memory | Each question is answered on its own in v1 |

Any of these could be discussed in interviews or reports as **possible future work**, but none will be built in v1.

---

## 11. Development Philosophy

1. **Functionality first, polish later.** Build the end-to-end pipeline working simply, then improve the UI. A plain screen that answers questions correctly comes before a beautiful screen that doesn't.
2. **Simple over clever.** Prefer direct, readable code over abstractions. If a design decision cannot be explained in a sentence or two, it is probably too complex.
3. **The owner must understand every part.** Each module should be small enough to walk through in an interview or viva.
4. **Security is not optional.** The validator and the read-only database role are core features, not extras, and the validator is tested from the start.
5. **Small, incremental steps.** Build one layer at a time, verify it works, and commit with clear messages.
6. **Single responsibility per module.** The LLM service only talks to Gemini; the validator only validates; the executor only executes.
7. **Document decisions.** Changes in scope or architecture are reflected in this file.

**Suggested build order**

1. Backend scaffold: minimal FastAPI application with a `/health` endpoint to verify the local development environment
2. Database schema + Faker seed data
3. SQL validator + its tests
4. DB executor (read-only connection, timeout, row limit)
5. Gemini NL → SQL service
6. Query pipeline + FastAPI endpoint
7. Minimal React frontend (input → SQL → table)
8. Charts + AI insight
9. Deployment (Supabase, Render, Vercel)
10. UI polish into a professional SaaS-style interface
11. Documentation, diagrams and demo material

---

## 12. Definition of a Successful Final Product

DataPilot v1 is complete when all of the following are true:

- [ ] A user can open the public URL, type a business question and receive the generated SQL, a results table, a suitable chart and a short insight.
- [ ] A representative set of example business questions (rankings, totals, trends, comparisons) produce correct answers.
- [ ] Destructive or unsafe SQL is always rejected, which is shown by validator unit tests covering both allowed and blocked cases.
- [ ] The backend connects to the database as a read-only role, so writes are impossible even if validation fails.
- [ ] No secrets are present in the repository or the frontend bundle.
- [ ] Errors (bad question, rejected SQL, timeout, empty results) are handled gracefully, with clear messages.
- [ ] The frontend looks and feels like a professional SaaS product: clean layout, consistent styling, loading states and a responsive design.
- [ ] Frontend (Vercel), backend (Render) and database (Supabase) are deployed and working together.
- [ ] The README explains what the project is, how it works, how to run it locally and includes screenshots or a demo.
- [ ] The owner can explain the full architecture, the safety model and every major design decision without notes.
