# DataPilot frontend

A minimal React + Vite page: type a business question, send it to the FastAPI `POST /query`
endpoint, and see the validated SQL and the result table. It holds no secrets.

## Run locally

```bash
cp .env.example .env.local   # VITE_API_BASE_URL=http://localhost:8000
npm install
npm run dev                  # http://localhost:5173
```

The backend must be running and must allow this origin (`CORS_ALLOWED_ORIGINS`, which
defaults to `http://localhost:5173` and `http://127.0.0.1:5173`).

## Scripts

- `npm test`: Vitest + React Testing Library (all API calls are mocked)
- `npm run build`: production build into `dist/`
- `npm run lint`: Oxlint

## Structure

```
src/
  services/api.js            the only code that calls the backend
  components/QuestionForm.jsx question box, Analyze button, example questions
  components/ResultsTable.jsx row count, truncation note, results table
  components/SqlViewer.jsx    the generated (validated) SQL
  components/ErrorMessage.jsx friendly error panel
  App.jsx                     page layout and request state
```
