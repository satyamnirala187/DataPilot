import { QUICK_QUESTIONS } from '../config/askContent.js'
import { WORKSPACE } from '../config/workspace.js'
import Icon from './Icon.jsx'
import QuestionForm from './QuestionForm.jsx'

/**
 * The Ask page before a question: greeting, the question box and the quick questions. Nothing
 * from a result appears here. onAsk(text) asks a quick question straight away.
 */
export default function AskHome({ onAsk, ...formProps }) {
  return (
    <div className="dp-page dp-ask-home">
      <h1 className="dp-greeting">Welcome back, {WORKSPACE.userName}</h1>
      <p className="dp-greeting-sub">What would you like to know today?</p>

      <QuestionForm {...formProps} />

      <section className="dp-quick" aria-labelledby="quick-heading">
        <h2 id="quick-heading" className="dp-section-label">
          Quick questions
        </h2>
        <ul className="dp-quick-list">
          {QUICK_QUESTIONS.map((text) => (
            <li key={text}>
              <button type="button" className="dp-quick-item" onClick={() => onAsk(text)}>
                <span>{text}</span>
                <Icon name="arrowRight" size={16} strokeWidth={1.8} />
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
