# DataPilot frontend

The React + Vite single-page app for DataPilot (UI V2). After the demo login it sends a question to
the FastAPI `POST /query` endpoint and shows the answer: a KPI or chart with summary figures, the AI
insight, the result table and the generated SQL. History and Saved Reports reopen stored answers,
and an answer can be saved as a titled report. It holds no secrets. For the project overview, architecture and full setup, see the
[root README](../README.md).

## Run locally

```bash
cp .env.example .env.local   # VITE_API_BASE_URL=http://localhost:8000
npm install
npm run dev                  # http://localhost:5173
```

The backend must be running and must allow this origin (`CORS_ALLOWED_ORIGINS`, which defaults
to `http://localhost:5173` and `http://127.0.0.1:5173`).

## Scripts

- `npm test`: Vitest + React Testing Library (all API calls are mocked)
- `npm run build`: production build into `dist/`
- `npm run lint`: Oxlint

`vercel.json` sets the production security headers, including the Content-Security-Policy (see
`docs/deployment.md`).

## Structure

```
src/
  AccessGate.jsx                  session check, login page, signed-in app
  App.jsx                         signed-in views, question and answer state
  services/api.js                 the only code that calls the backend
  hooks/useSavedReports.js        saved-report count, saved state and saving
  utils/                          formatting, result summaries, History and report helpers
  config/                         fixed Ask-page text and the demo workspace labels
  components/
    AppShell.jsx                  sidebar (icon rail on narrow screens) and top bar
    LoginPage.jsx, BootLoader.jsx login page and session-check loader
    AskHome.jsx, QuestionForm.jsx Ask page, question box, quick questions
    AnalysisProgress.jsx          progress while a question is answered
    ResultView.jsx                one answer: header, summary tiles, hero chart, insight, data, SQL
    HeroChart.jsx                 KPI, line and bar charts (Recharts)
    InsightCard.jsx, ResultsTable.jsx, SqlViewer.jsx, QuestionHeader.jsx
    ErrorMessage.jsx              safe error card with "Try instead" questions
    HistoryPage.jsx               History list, search and reopened analyses
    SavedReportsPage.jsx          Saved Reports list, search and reopened reports
    SaveReportAction.jsx, SaveReportDialog.jsx  Save Report and its title dialog
    PageLoader.jsx, Icon.jsx, Logo.jsx
```
