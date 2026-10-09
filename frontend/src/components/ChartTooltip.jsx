import { formatValue, humanizeLabel } from '../utils/format.js'

/** Recharts tooltip content: the point's label and its formatted value. */
export default function ChartTooltip({ active, payload, label, yKey, formatLabel = String }) {
  if (!active || !payload?.length) return null
  return (
    <div className="chart-tooltip">
      <p className="chart-tooltip-label">{formatLabel(label)}</p>
      <p className="chart-tooltip-value">
        {humanizeLabel(yKey)}: {formatValue(yKey, payload[0].value)}
      </p>
    </div>
  )
}
