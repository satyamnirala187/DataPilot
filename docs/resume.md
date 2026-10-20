# DataPilot Resume & Portfolio Summary

Ready-to-use wording for resumes, GitHub, portfolios and project submissions, written for DataPilot
v1.0.0 (tag `v1.0.0`). Every claim here matches the current repository; see the
[README](../README.md) and the documents in `docs/` for the evidence.

## 1. Project Heading and Description

**DataPilot | AI Business Data Analyst**

Full-stack AI analytics app that turns plain-English business questions into validated, read-only
SQL, charts and insights over a synthetic e-commerce database.

## 2. Resume Bullets (3)

- Built a full-stack AI analytics app with React, FastAPI and PostgreSQL that converts plain-English
  business questions into validated, read-only SQL using Gemini 3.6 Flash with a GroqCloud fallback,
  then renders deterministic KPI, bar, line or table outputs.
- Engineered layered safeguards for LLM-generated SQL using SQLGlot validation, a dedicated
  SELECT-only database role, authenticated demo access, rate limits and quota controls; added
  History and Saved Reports snapshots that reopen without rerunning AI or SQL.
- Shipped DataPilot on Vercel, Render and Supabase with client-side PDF/PPTX export; validated it
  with 1,521 passing backend tests, 584 passing frontend tests, a 24/25 (96%) Gemini-only live
  benchmark, and a 21/21 offline regression suite.

## 3. Tech Stack Line

**Tech:** Python, FastAPI, React, PostgreSQL, Supabase, Gemini API, GroqCloud, SQLGlot, Recharts,
pytest, Vitest, Vercel, Render

## 4. Short Project Summary

**A. Two sentences**

DataPilot lets non-technical users ask business questions in plain English and get answers from a
PostgreSQL database without writing SQL. Gemini 3.6 Flash writes the query (with a GroqCloud
fallback if Gemini cannot answer), a SQLGlot validator and a read-only database role keep it safe,
and a React frontend shows the result as a KPI, chart or table with the SQL that ran.

**B. Short version (about 70 words)**

DataPilot is a full-stack web app that answers business questions asked in plain English. A FastAPI
backend uses Gemini 3.6 Flash, with a GroqCloud fallback, to write PostgreSQL queries, validates
every one with SQLGlot and runs it through a read-only database role. The React frontend shows each
answer as a KPI, chart or table with an optional AI insight, and keeps History and Saved Reports. It
is deployed on Vercel, Render and Supabase.

**C. Longer version (about 150 words)**

Business users often need answers from a database but cannot write SQL. DataPilot lets them ask in
plain English instead. A FastAPI backend sends the question, the schema and fixed business
definitions to Gemini 3.6 Flash, which writes a PostgreSQL query; if Gemini cannot answer, GroqCloud
gets one attempt. The query is treated as untrusted: SQLGlot checks its syntax tree, and it runs only
through a read-only database role with a statement timeout and a 500-row cap. Fixed rules choose a
KPI, bar chart, line chart or table, and an optional AI insight summarises the rows. Each answer is
stored as a snapshot, so History and Saved Reports reopen it without rerunning AI or SQL; a saved
report can be removed again, and its analysis stays in History. Results can be exported as PDF or
PowerPoint in the browser. The hosted demo sits behind a private login, per-client rate limits and a
daily AI quota cap. The React frontend is deployed on Vercel, the API on Render and the synthetic
e-commerce database on Supabase.

## 5. GitHub Repository Description

**A. Short (repository "About" field)**

Ask business questions in plain English and get safe, read-only SQL, charts and tables.

**B. Portfolio description**

DataPilot is an AI-assisted analytics web app for a fictional e-commerce store. Users ask questions
in plain English; Gemini 3.6 Flash writes the SQL (GroqCloud is the fallback), SQLGlot and a
read-only PostgreSQL role keep it safe, and the app shows the result as a KPI, chart or table along
with the SQL that ran. History and Saved Reports reopen stored results without a new AI request, and
reports export to PDF or PowerPoint in the browser. Built with React, FastAPI and PostgreSQL,
deployed on Vercel, Render and Supabase behind a private demo login, and released as v1.0.0.

## 6. Academic Project Description

DataPilot addresses the difficulty non-technical users face in retrieving information from
relational databases. The system accepts business questions in natural language and uses a large
language model (Gemini 3.6 Flash, with a GroqCloud fallback for provider failures) to translate them
into PostgreSQL queries over a six-table synthetic e-commerce database containing 1,000 customers
and 5,000 orders. Because generated SQL is treated as untrusted input, each query, from either
provider, is parsed and checked with SQLGlot, then executed through a dedicated read-only database
role inside a read-only transaction with a statement timeout and a row limit. Results are presented
through deterministic visualization rules (KPI, bar chart, line chart or table) in a React frontend
that calls a FastAPI backend. Each answer is stored as a snapshot for History and Saved Reports, so
it can be reopened or exported as PDF or PowerPoint without rerunning the analysis. Access to the
hosted demo is protected by a private login, per-client rate limits and a daily cap on AI questions.
The implementation is supported by 1,521 backend tests, 584 frontend tests and a 25-question
benchmark: 24 of 25 questions passed in the live Gemini-only run, and the offline regression against
hand-written reference SQL passes all 21 answerable cases.

## 7. Interview Intro (30 to 45 seconds)

"DataPilot is an AI data analyst I built for a fictional e-commerce store. You ask a business
question in plain English, and it answers with a chart or table, a short insight and the exact SQL
it ran. Gemini writes the SQL, with GroqCloud as a fallback, but I treat that SQL as untrusted:
every query goes through a SQLGlot validator and then runs under a read-only database role, so even
a validator miss can't write data. Charts are chosen by fixed rules, not the model, and answers are
stored as snapshots, so reopening one from History doesn't call the AI again. It's React and
FastAPI, deployed on Vercel, Render and Supabase, with a 24-of-25 live benchmark."

## Notes on the numbers

- **Release:** DataPilot v1.0.0 is released. The annotated tag `v1.0.0` points to commit `7d336db`
  and has a GitHub Release. Later commits on `main` are documentation and media only.
- **Tests:** 1,521 backend tests (pytest) and 584 frontend tests (Vitest) passed on 2026-10-20. The
  backend run was environment-free: 40 database integration tests need the database URLs and were
  skipped.
- **Benchmark:** 25 questions, 21 answerable and 4 behaviour-only. The live Gemini-only baseline
  passed 24/25 (96%), with 1 correctness failure (`prod_profit_electronics`). It was assembled from
  several smaller runs (`docs/benchmark.md` §9). That case was fixed afterwards and re-checked, but
  the recorded baseline stays 24/25. GroqCloud did not take part in the baseline.
- **Offline regression:** 21/21 answerable cases pass. Offline mode runs trusted hand-written SQL,
  so this is a regression and consistency check of the expected answers, the validator, the
  executor and the chart rules, **not** a measure of Gemini's accuracy.
- **GroqCloud fallback:** verified in production once, as a single controlled smoke test on
  2026-10-17. It is not benchmarked and is not part of the 24/25.
- **Row cap:** at most 500 rows are returned; the query fetches one extra row only to report
  whether results were truncated.
- **Scope:** the dataset is synthetic (Faker, fixed seed) and the store is fictional, so no real
  customer or business data is involved. Access is one shared private demo login, not user
  accounts. DataPilot is a portfolio and demonstration project, not an enterprise analytics
  platform. Describe the benchmark as a pass count on a focused 25-question suite, never as general
  accuracy, and describe the safeguards as layered, not as making misuse impossible.
