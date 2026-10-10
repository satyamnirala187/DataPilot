# DataPilot frontend

The React + Vite single-page app for DataPilot. It sends a question to the FastAPI `POST /query`
endpoint and shows the AI insight, a KPI or chart, the result table and the generated SQL. It holds
no secrets. For the project overview, architecture and full setup, see the
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

## Structure

```
src/
  App.jsx                         page layout and request state
  services/api.js                 the only code that calls the backend
  utils/format.js                 number, currency, percentage, label and date formatting
  components/
    QuestionForm.jsx              question box, Analyze button, example questions
    InsightCard.jsx               AI insight, or a quiet "unavailable" note
    Visualization.jsx             draws the KPI or chart the backend chose
    KpiCard.jsx                   single-value display
    BarChartView.jsx              bar chart (Recharts)
    LineChartView.jsx             line chart (Recharts)
    ChartCard.jsx, ChartTooltip.jsx  shared chart frame and tooltip
    ResultsTable.jsx              row count, truncation note, result table
    SqlViewer.jsx                 generated SQL with Copy SQL
    ErrorMessage.jsx              friendly error panel
    Icon.jsx                      small inline SVG icons
```
