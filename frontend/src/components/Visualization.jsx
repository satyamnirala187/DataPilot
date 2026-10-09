import BarChartView from './BarChartView.jsx'
import KpiCard from './KpiCard.jsx'
import LineChartView from './LineChartView.jsx'

/**
 * Draws the KPI or chart the backend chose. It never picks a chart type or columns itself:
 * it only uses visualization.type, x_key and y_key. Anything else renders nothing (table only).
 */
export default function Visualization({ visualization, columns, rows }) {
  const type = visualization?.type
  const xIndex = columns.indexOf(visualization?.x_key)
  const yIndex = columns.indexOf(visualization?.y_key)

  if (type === 'kpi' && yIndex !== -1 && rows.length === 1) {
    return <KpiCard column={visualization.y_key} value={rows[0][yIndex]} />
  }
  if ((type !== 'bar' && type !== 'line') || xIndex === -1 || yIndex === -1) {
    return null
  }

  const data = rows.map((row) => ({ x: row[xIndex], y: row[yIndex] }))
  if (type === 'bar') {
    return <BarChartView data={data} xKey={visualization.x_key} yKey={visualization.y_key} />
  }
  return (
    <LineChartView data={inDateOrder(data)} xKey={visualization.x_key} yKey={visualization.y_key} />
  )
}

// A line must run oldest to newest. Rows already in that order are used as they are;
// otherwise a sorted copy is drawn (the table keeps the original order).
function inDateOrder(data) {
  const ascending = data.every((point, i) => i === 0 || String(data[i - 1].x) <= String(point.x))
  return ascending ? data : [...data].sort((a, b) => String(a.x).localeCompare(String(b.x)))
}
