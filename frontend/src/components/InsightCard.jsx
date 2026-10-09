import Icon from './Icon.jsx'

/** The AI-written business takeaway. The query already succeeded, so a missing insight is only a quiet note. */
export default function InsightCard({ insight }) {
  if (!insight) {
    return (
      <div className="insight-unavailable">
        <Icon name="sparkle" size={16} />
        <p>Insight temporarily unavailable.</p>
      </div>
    )
  }
  return (
    <section className="insight-card" aria-labelledby="insight-heading">
      <span className="insight-icon">
        <Icon name="sparkle" size={18} />
      </span>
      <div>
        <h2 id="insight-heading">AI Insight</h2>
        <p className="insight-text">{insight}</p>
      </div>
    </section>
  )
}
