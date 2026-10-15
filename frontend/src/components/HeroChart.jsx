import { useEffect, useId, useRef, useState } from 'react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { shortenLabel } from '../utils/format.js'
import { formatExactValue, formatSummaryValue } from '../utils/result.js'

const AXIS_TICK = { fill: 'var(--dp-hero-axis)', fontSize: 12.5 }
const BAR_TICK = { fill: 'var(--dp-hero-text)', fontSize: 14 }

/**
 * The blue hero card of a result: the KPI value, or the line or bar chart the backend chose.
 * view comes from describeResult (utils/result.js). Every value drawn is a value from the rows;
 * the Data table below is the full, readable version of the same numbers.
 */
export default function HeroChart({ view }) {
  if (view.kind === 'kpi') return <KpiHero view={view} />
  if (view.kind === 'line') return <LineHero view={view} />
  if (view.kind === 'bar') return <BarHero view={view} />
  return null
}

function KpiHero({ view }) {
  const id = useId()
  const compact = formatSummaryValue(view.yKey, view.value)
  const exact = formatExactValue(view.yKey, view.value)
  return (
    <section className="dp-hero dp-hero-kpi" aria-labelledby={id}>
      <h2 id={id} className="dp-hero-title">
        {view.label}
      </h2>
      <p className="dp-hero-kpi-value">{compact}</p>
      {/* The exact figure, when the short one is rounded. */}
      {exact !== compact && <p className="dp-hero-kpi-exact">{exact}</p>}
    </section>
  )
}

function HeroFrame({ title, pill, figure, note, children }) {
  const id = useId()
  return (
    <section className="dp-hero" aria-labelledby={id}>
      <div className="dp-hero-head">
        <h2 id={id} className="dp-hero-title">
          {title}
        </h2>
        <span className="dp-hero-pill">{pill}</span>
      </div>
      <p className="dp-hero-figure">
        <span className="dp-hero-value">{figure}</span>
        {note && <span className="dp-hero-note">{note}</span>}
      </p>
      {children}
    </section>
  )
}

function LineHero({ view }) {
  const { points, yKey, formatX, title, range } = view
  const lastIndex = points.findLastIndex((point) => point.y !== null)
  const animate = !prefersReducedMotion()
  return (
    <HeroFrame title={title} pill={range} figure={view.latest.value} note={view.change && `${view.change.text} vs previous`}>
      <div className="dp-hero-chart" role="img" aria-label={`Line chart of ${lowerFirst(title)}, ${range}`}>
        <ResponsiveContainer width="100%" height={240}>
          <AreaChart data={points} margin={{ top: 12, right: 12, bottom: 0, left: 0 }} accessibilityLayer={false}>
            <CartesianGrid vertical={false} stroke="var(--dp-hero-grid)" />
            <XAxis
              dataKey="x"
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              tickFormatter={formatX}
              minTickGap={18}
              tickMargin={10}
            />
            <YAxis
              width={68}
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value) => formatSummaryValue(yKey, value)}
            />
            <Tooltip
              cursor={{ stroke: 'var(--dp-hero-axis)', strokeOpacity: 0.5 }}
              content={<HeroTooltip yKey={yKey} formatLabel={formatX} />}
            />
            <Area
              type="linear"
              dataKey="y"
              stroke="var(--dp-hero-text)"
              strokeWidth={2.25}
              fill="var(--dp-hero-area)"
              fillOpacity={1}
              dot={({ index, cx, cy }) => <LineDot key={index} cx={cx} cy={cy} latest={index === lastIndex} />}
              activeDot={{ r: 5, fill: 'var(--dp-chart-highlight)', stroke: 'var(--dp-hero-text)', strokeWidth: 2 }}
              isAnimationActive={animate}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </HeroFrame>
  )
}

// Every point is a small dot; the latest one is larger and in the highlight colour.
function LineDot({ cx, cy, latest }) {
  if (typeof cx !== 'number' || typeof cy !== 'number') return <g />
  return latest ? (
    <circle className="dp-hero-dot-latest" cx={cx} cy={cy} r={5.5} fill="var(--dp-chart-highlight)" stroke="var(--dp-hero-text)" strokeWidth={2} />
  ) : (
    <circle cx={cx} cy={cy} r={3} fill="var(--dp-hero)" stroke="var(--dp-hero-text)" strokeWidth={2} />
  )
}

// Category names stay readable at any width: one horizontal bar per row, in the backend's order.
const BAR_ROW = 40
const BAR_ROW_DENSE = 30 // above 12 bars

function BarHero({ view }) {
  const { points, yKey, title, topIndex } = view
  const rowHeight = points.length > 12 ? BAR_ROW_DENSE : BAR_ROW
  const animate = !prefersReducedMotion()
  // Labels and value labels scale with the chart, so a narrow card still has room for its bars.
  const [chartRef, chartWidth] = useWidth()
  const labelWidth = chartWidth ? clamp(chartWidth * 0.36, 48, 150) : 150
  const valueSpace = chartWidth ? clamp(chartWidth * 0.22, 28, 76) : 76
  const labelChars = Math.max(5, Math.floor(labelWidth / 8))
  return (
    <HeroFrame title={title} pill={rowsText(points.length)} figure={view.top.value} note={`Highest: ${view.top.label}`}>
      <div ref={chartRef} className="dp-hero-chart" role="img" aria-label={`Bar chart of ${lowerFirst(title)}, ${rowsText(points.length)}`}>
        <ResponsiveContainer width="100%" height={points.length * rowHeight + 8}>
          <BarChart data={points} layout="vertical" margin={{ top: 4, right: valueSpace, bottom: 4, left: 0 }} accessibilityLayer={false}>
            <XAxis type="number" hide />
            <YAxis
              type="category"
              dataKey="x"
              width={labelWidth}
              interval={0}
              tick={BAR_TICK}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value) => shortenLabel(value, labelChars)}
            />
            <Tooltip cursor={{ fill: 'var(--dp-hero-grid)' }} content={<HeroTooltip yKey={yKey} />} />
            <Bar dataKey="y" radius={4} maxBarSize={22} isAnimationActive={animate}>
              {points.map((point, index) => (
                // The highest value shown is highlighted; the rest use the soft mark.
                <Cell key={index} fill={index === topIndex ? 'var(--dp-chart-highlight)' : 'var(--dp-hero-mark)'} />
              ))}
              <LabelList
                dataKey="y"
                position="right"
                offset={10}
                fill="var(--dp-hero-text)"
                fontSize={13}
                fontWeight={500}
                formatter={(value) => (typeof value === 'number' ? formatSummaryValue(yKey, value) : '')}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </HeroFrame>
  )
}

// The element's current width (0 until measured, e.g. where ResizeObserver is unavailable).
function useWidth() {
  const ref = useRef(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const element = ref.current
    if (!element || typeof ResizeObserver !== 'function') return undefined
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  return [ref, width]
}

/** Recharts tooltip content: the point's label and its exact value. */
function HeroTooltip({ active, payload, label, yKey, formatLabel = String }) {
  if (!active || !payload?.length) return null
  return (
    <div className="dp-chart-tooltip">
      <p className="dp-chart-tooltip-label">{formatLabel(label)}</p>
      <p className="dp-chart-tooltip-value">{formatExactValue(yKey, payload[0].value)}</p>
    </div>
  )
}

function clamp(value, min, max) {
  return Math.round(Math.min(max, Math.max(min, value)))
}

function rowsText(count) {
  return `${count} ${count === 1 ? 'row' : 'rows'}`
}

function lowerFirst(text) {
  return text.startsWith('AOV') ? text : text.charAt(0).toLowerCase() + text.slice(1)
}

function prefersReducedMotion() {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}
