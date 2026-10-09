import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { formatAxisValue, humanizeLabel, shortenLabel } from '../utils/format.js'
import ChartCard from './ChartCard.jsx'
import ChartTooltip from './ChartTooltip.jsx'

const ROW_HEIGHT = 40
const AXIS_TICK = { fontSize: 12, fill: 'var(--text-subtle)' }

/**
 * Bars for a category column (x) and a numeric column (y). data is [{ x, y }].
 * Bars run horizontally, so category names stay readable at any width: the chart grows taller
 * with more categories instead of squeezing or tilting the labels.
 */
export default function BarChartView({ data, xKey, yKey }) {
  const title = `${humanizeLabel(yKey)} by ${humanizeLabel(xKey)}`
  const height = Math.max(160, data.length * ROW_HEIGHT + 40)
  return (
    <ChartCard title={title} description={`${data.length} ${data.length === 1 ? 'bar' : 'bars'}, in the order returned`}>
      <div role="img" aria-label={`Bar chart of ${title}`}>
        <ResponsiveContainer width="100%" height={height}>
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 4 }}>
            <CartesianGrid stroke="var(--border)" strokeDasharray="4 4" horizontal={false} />
            <XAxis
              type="number"
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={{ stroke: 'var(--border-strong)' }}
              tickFormatter={(value) => formatAxisValue(yKey, value)}
            />
            <YAxis
              type="category"
              dataKey="x"
              width={124}
              interval={0}
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value) => shortenLabel(value, 18)}
            />
            <Tooltip cursor={{ fill: 'var(--accent-soft)' }} content={<ChartTooltip yKey={yKey} />} />
            <Bar dataKey="y" name={humanizeLabel(yKey)} fill="var(--accent)" radius={[0, 6, 6, 0]} maxBarSize={28} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  )
}
