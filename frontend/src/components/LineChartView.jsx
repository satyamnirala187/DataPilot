import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { dateFormatter, formatAxisValue, formatValue, humanizeLabel } from '../utils/format.js'

/** A line over a date column (x) and a numeric column (y). data is [{ x, y }], oldest first. */
export default function LineChartView({ data, xKey, yKey }) {
  const title = `${humanizeLabel(yKey)} by ${humanizeLabel(xKey)}`
  const formatDate = dateFormatter(data.map((point) => point.x))
  return (
    <section className="card" aria-labelledby="chart-heading">
      <h2 id="chart-heading">{title}</h2>
      <div role="img" aria-label={`Line chart of ${title}`}>
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={data} margin={{ top: 8, right: 24, bottom: 8, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="x" tick={{ fontSize: 12 }} tickFormatter={formatDate} minTickGap={16} />
            <YAxis width={72} tick={{ fontSize: 12 }} tickFormatter={(value) => formatAxisValue(yKey, value)} />
            <Tooltip
              labelFormatter={formatDate}
              formatter={(value) => [formatValue(yKey, value), humanizeLabel(yKey)]}
            />
            <Line type="monotone" dataKey="y" name={humanizeLabel(yKey)} stroke="var(--accent)" strokeWidth={2} dot={{ r: 3 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}
