import Icon from './Icon.jsx'

/**
 * A friendly error panel. Only shows the title and message, never raw response details.
 * tone "warning" is for temporary problems (rate limit, service busy, timeout, offline).
 */
export default function ErrorMessage({ title, message, tone = 'error' }) {
  return (
    <div className={`error-message tone-${tone}`} role="alert" data-tone={tone}>
      <Icon name={tone === 'warning' ? 'clock' : 'alert'} size={20} />
      <div>
        <h2 className="error-title">{title}</h2>
        <p className="error-text">{message}</p>
      </div>
    </div>
  )
}
