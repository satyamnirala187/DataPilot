/** A friendly error panel. Only shows the title and message, never raw response details. */
export default function ErrorMessage({ title, message }) {
  return (
    <div className="card error-message" role="alert">
      <h2 className="error-title">{title}</h2>
      <p>{message}</p>
    </div>
  )
}
