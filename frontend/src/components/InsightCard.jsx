/** The AI-written insight. The query already succeeded, so a missing insight is only a quiet note. */
export default function InsightCard({ insight }) {
  if (!insight) {
    return <p className="insight-unavailable">Insight temporarily unavailable.</p>
  }
  return (
    <section className="card insight-card" aria-labelledby="insight-heading">
      <h2 id="insight-heading">AI Insight</h2>
      <p>{insight}</p>
    </section>
  )
}
