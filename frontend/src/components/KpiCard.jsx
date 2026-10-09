import { formatValue, humanizeLabel } from '../utils/format.js'

/** One aggregate value, shown large with its column name as the label. */
export default function KpiCard({ column, value }) {
  return (
    <section className="card kpi-card" aria-labelledby="kpi-label">
      <h2 id="kpi-label" className="kpi-label">
        {humanizeLabel(column)}
      </h2>
      <p className="kpi-value">{formatValue(column, value)}</p>
    </section>
  )
}
