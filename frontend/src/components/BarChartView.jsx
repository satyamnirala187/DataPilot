import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { formatAxisValue, formatValue, humanizeLabel, shortenLabel } from '../utils/format.js'

const PIXELS_PER_BAR = 56

/** Bars for a category column (x) and a numeric column (y). data is [{ x, y }]. */
export default function BarChartView({ data, xKey, yKey }) {
  const title = `${humanizeLabel(yKey)} by ${humanizeLabel(xKey)}`
  return (
    <section className="card" aria-labelledby="chart-heading">
      <h2 id="chart-heading">{title}</h2>
      {/* Many bars get a minimum width and scroll sideways instead of squashing. */}
      <div className="chart-scroll">
        <div role="img" aria-label={`Bar chart of ${title}`} style={{ minWidth: data.length * PIXELS_PER_BAR }}>
          <ResponsiveContainer width="100%" height={340}>
            <BarChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="x"
                interval={0}
                angle={-30}
                textAnchor="end"
                height={72}
                tick={{ fontSize: 12 }}
                tickFormatter={(value) => shortenLabel(value)}
              />
              <YAxis width={72} tick={{ fontSize: 12 }} tickFormatter={(value) => formatAxisValue(yKey, value)} />
              <Tooltip formatter={(value) => [formatValue(yKey, value), humanizeLabel(yKey)]} />
              <Bar dataKey="y" name={humanizeLabel(yKey)} fill="var(--accent)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </section>
  )
}
