import { useMemo } from 'react'
import { describeResult } from '../utils/result.js'
import HeroChart from './HeroChart.jsx'
import Icon from './Icon.jsx'
import InsightCard from './InsightCard.jsx'
import QuestionHeader from './QuestionHeader.jsx'
import ResultsTable from './ResultsTable.jsx'
import SqlViewer from './SqlViewer.jsx'

/**
 * One answer, in the V2 layout: question and headline, summary tiles and the hero card, the
 * insight and the Data card, then the SQL. It takes any result-shaped object (a /query answer,
 * and later a History or Saved Reports analysis) and reads only question, sql, columns, rows,
 * row_count, truncated, visualization and insight. Extra fields are ignored; nothing is changed.
 * actions: optional controls shown under the result (e.g. Save Report), supplied by the caller,
 * so this view never talks to the API itself.
 */
export default function ResultView({ result, actions = null }) {
  const view = useMemo(() => describeResult(result), [result])
  const columns = Array.isArray(result.columns) ? result.columns : []
  const rows = Array.isArray(result.rows) ? result.rows : []
  const rowCount = typeof result.row_count === 'number' ? result.row_count : rows.length
  const hasHero = view.kind === 'kpi' || view.kind === 'line' || view.kind === 'bar'
  // Two-column results sit beside the insight; wider tables get the full width.
  const stacked = !hasHero || columns.length > 3

  return (
    <div className="dp-result">
      <QuestionHeader question={result.question} headline={view.headline} />

      {view.kind === 'empty' ? (
        <div className="dp-card dp-empty-result">
          <span className="dp-icon-chip">
            <Icon name="search" size={20} strokeWidth={1.7} />
          </span>
          <div>
            <h2 className="dp-card-title">No matching data was found for this question.</h2>
            <p className="dp-empty-result-text">The query ran successfully and returned 0 rows.</p>
          </div>
        </div>
      ) : (
        <>
          {hasHero && (
            <div className="dp-result-top">
              {view.tiles?.length > 0 && (
                <ul className="dp-tiles" aria-label="Summary of the rows shown">
                  {view.tiles.map((tile) => (
                    <li key={tile.label} className="dp-tile">
                      <p className="dp-tile-label">{tile.label}</p>
                      <p className="dp-tile-value">{tile.value}</p>
                      <p className="dp-tile-note">{tile.note}</p>
                    </li>
                  ))}
                </ul>
              )}
              <HeroChart view={view} />
            </div>
          )}
          <div className={stacked ? 'dp-result-evidence dp-result-evidence-stacked' : 'dp-result-evidence'}>
            <InsightCard insight={result.insight} rowCount={rowCount} />
            <ResultsTable columns={columns} rows={rows} rowCount={rowCount} truncated={result.truncated === true} />
          </div>
        </>
      )}

      {typeof result.sql === 'string' && result.sql && <SqlViewer sql={result.sql} />}

      {actions && <div className="dp-result-actions">{actions}</div>}
    </div>
  )
}
