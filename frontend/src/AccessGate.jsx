import { useEffect, useState } from 'react'
import App from './App.jsx'
import LoginPage, { AccessCard } from './components/LoginPage.jsx'
import { checkSession, getToken, logout } from './services/api.js'

const SESSION_EXPIRED = 'Your session has expired. Please log in again.'

/**
 * The app's root: DataPilot is shown only to a signed-in demo user.
 *
 * checking    a stored token is being verified with GET /auth/session (nothing else is shown)
 * signedOut   the login page
 * signedIn    DataPilot
 * unreachable the session could not be checked (network or server error); the token is kept
 *
 * This is only the user experience. The backend refuses every /query without a valid token.
 */
export default function AccessGate() {
  const [status, setStatus] = useState(() => (getToken() ? 'checking' : 'signedOut'))
  const [notice, setNotice] = useState(null)

  useEffect(() => {
    if (status !== 'checking') return undefined
    let cancelled = false
    checkSession()
      .then((valid) => {
        if (!cancelled) setStatus(valid ? 'signedIn' : 'signedOut')
      })
      .catch(() => {
        if (!cancelled) setStatus('unreachable')
      })
    return () => {
      cancelled = true
    }
  }, [status])

  function handleSignedIn() {
    setNotice(null)
    setStatus('signedIn')
  }

  function handleLogout() {
    void logout() // clears the token at once; the server request finishes (or fails) on its own
    setNotice(null)
    setStatus('signedOut')
  }

  function handleSessionExpired() {
    setNotice(SESSION_EXPIRED)
    setStatus('signedOut')
  }

  if (status === 'signedIn') return <App onLogout={handleLogout} onSessionExpired={handleSessionExpired} />
  if (status === 'signedOut') return <LoginPage onSignedIn={handleSignedIn} notice={notice} />

  if (status === 'unreachable') {
    return (
      <AccessCard labelledBy="access-heading">
        <h1 id="access-heading" className="gate-title">
          Private Demo Access
        </h1>
        <p className="gate-error" role="alert">
          Unable to reach DataPilot right now. Please try again.
        </p>
        <button type="button" className="primary-button gate-submit" onClick={() => setStatus('checking')}>
          Try again
        </button>
      </AccessCard>
    )
  }

  return (
    <AccessCard labelledBy="access-heading">
      <h1 id="access-heading" className="gate-title">
        Private Demo Access
      </h1>
      <p className="gate-checking" role="status">
        <span className="status-dot" aria-hidden="true" />
        Checking access...
      </p>
    </AccessCard>
  )
}
