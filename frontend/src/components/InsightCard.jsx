import { useId } from 'react'
import Icon from './Icon.jsx'

/**
 * The insight the backend wrote for this result, exactly as sent; nothing is requested here. The
 * query itself succeeded, so a missing insight is a quiet note, not an error.
 */
export default function InsightCard({ insight, rowCount }) {
  const id = useId()
  const text = typeof insight === 'string' ? insight.trim() : ''
  return (
    <section className="dp-card dp-insight" aria-labelledby={id}>
      <div className="dp-card-head">
        <span className="dp-icon-chip">
          <Icon name="bulb" size={20} strokeWidth={1.7} />
        </span>
        <h2 id={id} className="dp-card-title">
          Insight
        </h2>
      </div>
      {text ? (
        <p className="dp-insight-text">{text}</p>
      ) : (
        <p className="dp-insight-text dp-insight-unavailable">AI insight is unavailable for this result.</p>
      )}
      <p className="dp-insight-meta">
        Answered from the sales database · {rowCount} {rowCount === 1 ? 'row' : 'rows'}
      </p>
    </section>
  )
}
