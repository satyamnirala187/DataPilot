import Icon from './Icon.jsx'

/** The row-count summary and the results table. Values are shown as returned, without formatting. */
export default function ResultsTable({ columns, rows, rowCount, truncated }) {
  const numericColumns = columns.map((_, index) => isNumericColumn(rows, index))

  return (
    <section className="card" aria-labelledby="results-heading">
      <div className="card-header">
        <h2 id="results-heading" className="card-title">
          <Icon name="table" size={16} />
          Results
        </h2>
        <p className="meta">
          <span className="badge">
            {rowCount} {rowCount === 1 ? 'row' : 'rows'}
          </span>
          {truncated && (
            <span className="badge badge-warning">Results truncated: only the first {rowCount} rows are shown.</span>
          )}
        </p>
      </div>

      {rows.length === 0 ? (
        <p className="empty-result">The query ran successfully but returned no rows.</p>
      ) : (
        // tabIndex lets keyboard users scroll a wide table.
        <div className="table-scroll" role="region" aria-labelledby="results-heading" tabIndex={0}>
          <table>
            <thead>
              <tr>
                {columns.map((column, index) => (
                  <th key={index} scope="col" className={numericColumns[index] ? 'numeric' : undefined}>
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((value, cellIndex) => (
                    <td key={cellIndex} className={numericColumns[cellIndex] ? 'numeric' : undefined}>
                      {formatCell(value)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

// Right-align columns whose values are all numbers, so digits line up.
function isNumericColumn(rows, index) {
  const values = rows.map((row) => row[index]).filter((value) => value !== null && value !== undefined)
  return values.length > 0 && values.every((value) => typeof value === 'number')
}

function formatCell(value) {
  if (value === null || value === undefined) {
    return <span className="null-value">NULL</span>
  }
  if (typeof value === 'object') {
    return JSON.stringify(value)
  }
  return String(value)
}
