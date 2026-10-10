# DataPilot Resume & Portfolio Summary

Ready-to-use wording for resumes, GitHub, portfolios and project submissions. Every claim here
matches the current repository; see the [README](../README.md) and the documents in `docs/` for
the evidence.

## 1. One-Line Project Description

1. Full-stack AI analytics app that turns plain-English business questions into validated,
   read-only PostgreSQL queries and shows the results as KPIs, charts and tables.
2. Natural-language business analytics web app built with React, FastAPI, Gemini and PostgreSQL,
   with layered SQL safety and deterministic chart selection.

## 2. Short Project Summary

**A. Two sentences**

DataPilot lets non-technical users ask business questions in plain English and get answers from a
PostgreSQL database without writing SQL. Gemini generates the query, a SQLGlot validator and a
read-only database role keep it safe, and a React frontend shows the result as a KPI, chart or table
with the SQL that ran.

**B. Short version (about 60 words)**

DataPilot is a full-stack web app that answers business questions asked in plain English. A FastAPI
backend uses Gemini to write PostgreSQL queries, validates them with SQLGlot and runs them through a
read-only database role. The React frontend shows each answer as a KPI, bar chart, line chart or
table, with an optional AI insight. It is deployed on Vercel, Render and Supabase.

**C. Longer version (about 100 words)**

Business users often need answers from a database but cannot write SQL. DataPilot lets them ask
questions in plain English instead. A FastAPI backend sends the question, the schema and fixed
business definitions to Gemini, which writes a PostgreSQL query. The query is treated as untrusted:
SQLGlot checks its syntax tree, and it runs only through a read-only database role with a
statement timeout and a 500-row cap. Fixed rules choose a KPI, bar chart, line chart or table, and
an optional AI insight summarises the rows. The React frontend is deployed on Vercel, the API on
Render, and the synthetic e-commerce database on Supabase.

## 3. Resume Bullets (4)

- **Built** a full-stack AI analytics app (React, FastAPI, PostgreSQL) that converts plain-English
  business questions into SQL with Gemini and returns results as KPIs, charts and tables, with the
  generated SQL and an optional AI insight.
- **Designed** a defence-in-depth safety model for LLM-generated SQL: SQLGlot syntax-tree
  validation for a single read-only query, table and function allowlists, bounded query results, a
  dedicated read-only PostgreSQL role and statement timeouts.
- **Built** 656 backend and 66 frontend tests plus a 25-question business benchmark evaluated by
  returned values; all 21 answerable reference-SQL cases pass the offline regression suite.
- **Deployed** the frontend on Vercel, the API on Render and the database on Supabase, with
  restricted CORS, per-client rate limiting, structured error handling and server-side secrets.

## 4. Compact Resume Version (3 bullets)

- Built a full-stack AI analytics app (React, FastAPI, PostgreSQL) that turns plain-English business
  questions into SQL with Gemini and shows answers as KPIs, charts and tables.
- Secured LLM-generated SQL with SQLGlot validation, a read-only PostgreSQL role, statement timeouts
  and a 500-row cap; chart selection uses deterministic rules, not the LLM.
- Tested with 656 backend and 66 frontend tests plus a 25-question regression suite, with 21/21
  answerable reference-SQL cases passing offline; deployed on Vercel, Render and Supabase.

## 5. Tech Stack Line

**Tech:** Python, FastAPI, React, Vite, Recharts, PostgreSQL, Supabase, Gemini API, SQLGlot, pytest,
Vitest, Vercel, Render

## 6. GitHub Repository Description

**A. Short (repository "About" field)**

Ask business questions in plain English and get safe, read-only SQL, charts and tables.

**B. Portfolio description**

DataPilot is an AI-assisted analytics web app for a fictional e-commerce store. Users ask questions
in plain English; Gemini writes the SQL, SQLGlot and a read-only PostgreSQL role keep it safe, and
the app shows the result as a KPI, chart or table along with the SQL that ran. Built with React,
FastAPI and PostgreSQL, and deployed on Vercel, Render and Supabase.

## 7. Academic Project Description

DataPilot addresses the difficulty non-technical users face in retrieving information from
relational databases. The system accepts business questions in natural language and uses a large
language model (Gemini) to translate them into PostgreSQL queries over a six-table synthetic
e-commerce database containing 1,000 customers and 5,000 orders. Because generated SQL is treated
as untrusted input, each query is parsed and checked with SQLGlot, then executed through a
dedicated read-only database role inside a read-only transaction with a statement timeout and a
row limit. Results are presented through deterministic visualization rules (KPI, bar chart, line
chart or table) in a React frontend that calls a FastAPI backend. The implementation is supported by
656 backend tests, 66 frontend tests and a 25-question benchmark that compares results against
hand-written reference SQL.

## 8. Interview Intro (30 to 45 seconds)

"DataPilot is an AI data analyst I built for a fictional e-commerce company. You ask a business
question in plain English, like 'what are our top five categories by revenue', and it answers with a
chart, a table and a short insight. Gemini writes the SQL, but I treat that SQL as untrusted: a
SQLGlot validator only allows read-only queries on six known tables, and it runs through a dedicated
read-only database role, so database permissions provide a second safety boundary even if validation
misses something. Charts are picked by fixed rules, not the model. It's React and FastAPI, deployed
on Vercel, Render and Supabase, with over 700 automated tests."

## Notes on the numbers

- **Tests:** 656 backend tests (pytest) and 66 frontend tests (Vitest) pass. A few backend
  integration tests need the database and are skipped without it.
- **Benchmark:** 25 questions, 21 answerable and 4 behaviour-only. Offline mode runs trusted
  hand-written SQL, so "21/21 offline" is a regression and consistency check, **not** a measure of
  Gemini's accuracy. The live Gemini benchmark has not been run yet. Do not describe the project as
  "100% accurate".
- **Row cap:** at most 500 rows are returned; the query fetches one extra row only to report
  whether results were truncated.
