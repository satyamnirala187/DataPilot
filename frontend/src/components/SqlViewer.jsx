/** Shows the validated SQL exactly as the backend ran it. It is display-only. */
export default function SqlViewer({ sql }) {
  return (
    <section className="card" aria-labelledby="sql-heading">
      <h2 id="sql-heading">Generated SQL</h2>
      <pre className="sql-block">
        <code>{sql}</code>
      </pre>
    </section>
  )
}
