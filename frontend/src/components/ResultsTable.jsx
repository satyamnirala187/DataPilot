/** The row-count summary and the results table. */
export default function ResultsTable({ columns, rows, rowCount, truncated }) {
  return (
    <section className="card" aria-labelledby="results-heading">
      <h2 id="results-heading">Results</h2>
      <p className="result-summary">
        {rowCount} {rowCount === 1 ? 'row' : 'rows'}
        {truncated && (
          <span className="truncated-note"> · Results truncated: only the first {rowCount} rows are shown.</span>
        )}
      </p>

      {rows.length === 0 ? (
        <p className="empty-result">The query ran successfully but returned no rows.</p>
      ) : (
        // tabIndex lets keyboard users scroll a wide table.
        <div className="table-scroll" role="region" aria-labelledby="results-heading" tabIndex={0}>
          <table>
            <thead>
              <tr>
                {columns.map((column, index) => (
                  <th key={index} scope="col">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((value, cellIndex) => (
                    <td key={cellIndex}>{formatCell(value)}</td>
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

function formatCell(value) {
  if (value === null || value === undefined) {
    return <span className="null-value">NULL</span>
  }
  if (typeof value === 'object') {
    return JSON.stringify(value)
  }
  return String(value)
}
