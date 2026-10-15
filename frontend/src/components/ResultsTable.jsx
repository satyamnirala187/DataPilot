import { useId, useMemo } from 'react'
import { describeColumns } from '../utils/result.js'

/**
 * The Data card: every column and row the backend returned, in its order, and nothing else.
 * Numbers use Indian grouping (₹ and % only from the column name), plain dates read like
 * '8 Oct 2026', and a missing value shows as NULL. A wide table scrolls inside the card.
 */
export default function ResultsTable({ columns, rows, rowCount, truncated }) {
  const id = useId()
  const shown = useMemo(() => describeColumns(columns, rows), [columns, rows])

  return (
    <section className="dp-card dp-data" aria-labelledby={id}>
      <div className="dp-data-head">
        <h2 id={id} className="dp-card-title">
          Data
        </h2>
        <p className="dp-data-count">
          {rowCount} {rowCount === 1 ? 'row' : 'rows'}
        </p>
      </div>
      {truncated && (
        <p className="dp-data-note">
          Only the first {rowCount} rows are shown. The full result has more rows than DataPilot displays.
        </p>
      )}

      {/* tabIndex lets keyboard users scroll a wide table. */}
      <div className="dp-table-scroll" role="region" aria-label="Data table" tabIndex={0}>
        <table className="dp-table">
          <thead>
            <tr>
              {shown.map((column, index) => (
                <th key={index} scope="col" className={column.numeric ? 'dp-num' : undefined}>
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {shown.map((column, index) => {
                  const text = column.format(row?.[index])
                  return (
                    <td key={index} className={column.numeric ? 'dp-num' : undefined}>
                      {text === null ? <span className="dp-null">NULL</span> : text}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
