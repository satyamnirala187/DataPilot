import { QUICK_QUESTIONS } from '../config/askContent.js'
import Icon from './Icon.jsx'

// Backend codes for a question whose generated query was stopped: by the SQL safety checks
// (unsafe_sql) or by the database's read-only role (query_not_allowed). The backend does not say
// why, so the wording stays general.
const CANNOT_RUN_CODES = new Set(['unsafe_sql', 'query_not_allowed'])
const CANNOT_RUN_TITLE = "This question can't be run"
const CANNOT_RUN_TEXT =
  'DataPilot only runs read-only queries that pass its safety checks, and the query for this ' +
  'question did not. Nothing in your data was changed. Rephrase the question to ask for a result instead.'

/**
 * A failed question. Shows only the safe title and message from the API client (or the fixed
 * wording above), never raw response details. tone "warning" is for temporary problems (rate
 * limit, service busy, timeout, offline). A stopped question also offers questions to try,
 * which are asked through the normal question path by onTryQuestion. children: optional actions
 * shown under the message (e.g. "Try again").
 */
export default function ErrorMessage({ title, message, tone = 'error', code = null, onTryQuestion, children }) {
  const cannotRun = CANNOT_RUN_CODES.has(code)
  return (
    <div className="dp-card dp-error-card">
      <div className="dp-error-main" role="alert" data-tone={tone}>
        <div className="dp-error-head">
          <span className="dp-icon-chip dp-error-chip">
            <Icon name={tone === 'warning' ? 'clock' : 'alertCircle'} size={22} strokeWidth={1.9} />
          </span>
          <h2 className="dp-error-title">{cannotRun ? CANNOT_RUN_TITLE : title}</h2>
        </div>
        <p className="dp-error-text">{cannotRun ? CANNOT_RUN_TEXT : message}</p>
      </div>

      {children && <div className="dp-error-actions">{children}</div>}

      {cannotRun && onTryQuestion && (
        <div className="dp-try">
          <h3 className="dp-try-title">Try instead</h3>
          <ul className="dp-try-list">
            {QUICK_QUESTIONS.map((text) => (
              <li key={text}>
                <button type="button" className="dp-try-item" onClick={() => onTryQuestion(text)}>
                  {text}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
