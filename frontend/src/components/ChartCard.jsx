import Icon from './Icon.jsx'

/** The card around a chart: heading, a short description, then the chart itself. */
export default function ChartCard({ title, description, children }) {
  return (
    <section className="card" aria-labelledby="chart-heading">
      <div className="card-header">
        <div>
          <h2 id="chart-heading" className="card-title">
            <Icon name="chart" size={16} />
            {title}
          </h2>
          <p className="card-caption">{description}</p>
        </div>
      </div>
      <div className="chart-scroll">{children}</div>
    </section>
  )
}
