import { useEffect, useState } from 'react'
import { ANALYSIS_STEPS, STEP_STARTS_MS } from '../config/askContent.js'

/**
 * Shown while a question is being answered: the question, then a card with a progress line and
 * the five steps. Visual only; the page's status line announces "Analyzing your question" once.
 * It is removed when the request ends, which stops its timers.
 */
export default function AnalysisProgress({ question }) {
  const [active, setActive] = useState(0)

  useEffect(() => {
    const timers = STEP_STARTS_MS.map((delay, index) => setTimeout(() => setActive(index + 1), delay))
    return () => timers.forEach(clearTimeout)
  }, [])

  return (
    <div className="dp-page dp-page-result">
      <div className="dp-question-header">
        <p className="dp-question-label">Your question</p>
        <h1 className="dp-question-title">{question}</h1>
      </div>

      <section className="dp-card dp-analysis" aria-labelledby="analysis-heading">
        <h2 id="analysis-heading" className="dp-analysis-title">
          Analyzing your question
        </h2>
        <p className="dp-analysis-note">This usually takes a few seconds.</p>
        <div className="dp-analysis-bar" aria-hidden="true">
          <span style={{ width: `${(active / ANALYSIS_STEPS.length) * 100}%` }} />
        </div>
        <ol className="dp-analysis-steps">
          {ANALYSIS_STEPS.map((label, index) => {
            const state = index < active ? 'done' : index === active ? 'active' : 'todo'
            return (
              <li key={label} data-state={state} aria-current={state === 'active' ? 'step' : undefined}>
                <StepIcon state={state} />
                {label}
              </li>
            )
          })}
        </ol>
      </section>
    </div>
  )
}

function StepIcon({ state }) {
  return (
    <svg className="dp-step-icon" width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {state === 'done' ? (
        <>
          <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" strokeWidth="2.2" />
          <path d="M8 12.5l2.7 2.7L16 9.5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
        </>
      ) : (
        <>
          <circle className="dp-step-ring" cx="12" cy="12" r="9" fill="none" strokeWidth="2.2" />
          {state === 'active' && <circle cx="12" cy="12" r="4.5" fill="currentColor" />}
        </>
      )}
    </svg>
  )
}
