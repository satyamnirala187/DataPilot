import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { dateFormatter, formatAxisValue, humanizeLabel } from '../utils/format.js'
import ChartCard from './ChartCard.jsx'
import ChartTooltip from './ChartTooltip.jsx'

const AXIS_TICK = { fontSize: 12, fill: 'var(--text-subtle)' }

/** A line over a date column (x) and a numeric column (y). data is [{ x, y }], oldest first. */
export default function LineChartView({ data, xKey, yKey }) {
  const title = `${humanizeLabel(yKey)} by ${humanizeLabel(xKey)}`
  const formatDate = dateFormatter(data.map((point) => point.x))
  const range = data.length ? `${formatDate(data[0].x)} – ${formatDate(data[data.length - 1].x)}` : ''
  return (
    <ChartCard title={title} description={`${data.length} points, ${range}`}>
      <div role="img" aria-label={`Line chart of ${title}`}>
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={data} margin={{ top: 8, right: 24, bottom: 8, left: 8 }}>
            <CartesianGrid stroke="var(--border)" strokeDasharray="4 4" vertical={false} />
            <XAxis
              dataKey="x"
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={{ stroke: 'var(--border-strong)' }}
              tickFormatter={formatDate}
              minTickGap={16}
            />
            <YAxis
              width={72}
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value) => formatAxisValue(yKey, value)}
            />
            <Tooltip
              cursor={{ stroke: 'var(--border-strong)' }}
              content={<ChartTooltip yKey={yKey} formatLabel={formatDate} />}
            />
            <Line
              type="monotone"
              dataKey="y"
              name={humanizeLabel(yKey)}
              stroke="var(--accent)"
              strokeWidth={2.5}
              dot={{ r: 3, fill: 'var(--surface)', strokeWidth: 2 }}
              activeDot={{ r: 5 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  )
}
