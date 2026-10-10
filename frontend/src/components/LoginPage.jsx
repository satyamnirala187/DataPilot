import { useEffect, useRef, useState } from 'react'
import Icon from './Icon.jsx'
import { ApiError, login } from '../services/api.js'

/** The centred card used by every access screen: login, checking access, backend unreachable. */
export function AccessCard({ labelledBy, children }) {
  return (
    <div className="gate">
      <main className="gate-card" aria-labelledby={labelledBy}>
        <div className="gate-brand">
          <span className="brand-mark">
            <Icon name="logo" size={20} />
          </span>
          <div>
            <p className="gate-name">DataPilot</p>
            <p className="brand-tagline">AI-powered business data analyst</p>
          </div>
        </div>
        {children}
      </main>
    </div>
  )
}

/**
 * Private demo login: one shared username and password, checked by the backend. On success the
 * token is stored by login() and onSignedIn() opens DataPilot. No accounts, sign-up or resets.
 */
export default function LoginPage({ onSignedIn, notice = null }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const usernameRef = useRef(null)
  const passwordRef = useRef(null)
  // Guards against a second submit before React has re-rendered the disabled button.
  const inFlight = useRef(false)
  // After a failed attempt the fields are re-enabled; then the cursor goes back to the password.
  const refocusPassword = useRef(false)

  useEffect(() => {
    usernameRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!submitting && refocusPassword.current) {
      refocusPassword.current = false
      passwordRef.current?.focus()
    }
  }, [submitting])

  async function handleSubmit(event) {
    event.preventDefault()
    if (inFlight.current) return
    if (!username.trim() || !password) {
      setError('Please enter your username and password.')
      ;(username.trim() ? passwordRef : usernameRef).current?.focus()
      return
    }

    inFlight.current = true
    setSubmitting(true)
    setError(null)
    try {
      await login(username.trim(), password)
      onSignedIn()
    } catch (err) {
      setPassword('')
      refocusPassword.current = true
      setError(err instanceof ApiError ? err.message : 'Something went wrong while signing in. Please try again.')
    } finally {
      inFlight.current = false
      setSubmitting(false)
    }
  }

  return (
    <AccessCard labelledBy="login-heading">
      <h1 id="login-heading" className="gate-title">
        Private Demo Access
      </h1>
      <p className="gate-intro">Sign in with the demo credentials you were given.</p>

      {notice && (
        <p className="gate-notice" role="status">
          {notice}
        </p>
      )}

      <form className="gate-form" onSubmit={handleSubmit} noValidate aria-busy={submitting}>
        <label htmlFor="demo-username">Username</label>
        <input
          id="demo-username"
          ref={usernameRef}
          name="username"
          type="text"
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          disabled={submitting}
          aria-invalid={Boolean(error) || undefined}
          aria-describedby={error ? 'login-error' : undefined}
        />

        <label htmlFor="demo-password">Password</label>
        <input
          id="demo-password"
          ref={passwordRef}
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          disabled={submitting}
          aria-invalid={Boolean(error) || undefined}
          aria-describedby={error ? 'login-error' : undefined}
        />

        {error && (
          <p id="login-error" className="gate-error" role="alert">
            {error}
          </p>
        )}

        <button type="submit" className="primary-button gate-submit" disabled={submitting}>
          {submitting && <span className="spinner" aria-hidden="true" />}
          {submitting ? 'Signing in...' : 'Enter DataPilot'}
        </button>
      </form>

      <p className="gate-footnote">
        <Icon name="shield" size={14} />
        Private portfolio demonstration
      </p>
    </AccessCard>
  )
}
