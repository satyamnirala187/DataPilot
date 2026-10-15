// The fixed text of the Ask page: example questions and the analysis steps. Questions only, never
// answers: every example is one the store data can answer, asked through the normal question path.

// The question box's placeholder cycle: a prompt, then example questions.
export const PLACEHOLDERS = [
  'Ask your question…',
  'What is our average order value?',
  'Which customers spend the most?',
  'What is our profit margin?',
  'What are our top 10 products?',
]

// The quick questions under the box.
export const QUICK_QUESTIONS = [
  'What is our total revenue?',
  'Show monthly revenue trends.',
  'Which category generated the most revenue?',
  'Who are our top customers?',
]

// The analysis steps, in the order POST /query does them.
export const ANALYSIS_STEPS = [
  'Understanding your question',
  'Generating the SQL query',
  'Validating that the query is read-only',
  'Running on the sales database',
  'Preparing the answer',
]

// When each later step becomes the active one, in ms after the question was sent. A rough guide to
// a typical answer, not a measurement: /query is one request and reports no progress. The last step
// stays active until the answer arrives, so the bar never reaches the end while still waiting.
export const STEP_STARTS_MS = [800, 3000, 3800, 5000]
