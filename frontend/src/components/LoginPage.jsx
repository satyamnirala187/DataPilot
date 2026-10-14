import { useEffect, useRef, useState } from 'react'
import Icon from './Icon.jsx'
import Logo from './Logo.jsx'
import { ApiError, login } from '../services/api.js'

const EMPTY_FIELDS = 'Enter your username and password.'
const LOGIN_FAILED = 'Something went wrong while signing in. Please try again.'

/**
 * Private demo login: one shared username and password, checked by the backend. On success the
 * token is stored by login() and onSignedIn() opens DataPilot. No accounts, sign-up, resets or
 * "remember me": the session lasts as long as the backend allows, in this tab only.
 */
export default function LoginPage({ onSignedIn, notice = null }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  // { message, fields }: fields is true when the entered details are the problem (empty or wrong),
  // so the inputs are marked invalid; not for rate limits or an unreachable service.
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
      setError({ message: EMPTY_FIELDS, fields: true })
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
      setError(
        err instanceof ApiError
          ? { message: err.message, fields: err.status === 401 }
          : { message: LOGIN_FAILED, fields: false },
      )
    } finally {
      inFlight.current = false
      setSubmitting(false)
    }
  }

  // Typing clears the error, as in the approved design.
  function edit(setValue) {
    return (event) => {
      setValue(event.target.value)
      if (error) setError(null)
    }
  }

  const invalid = error?.fields || undefined
  const describedBy = error ? 'login-error' : undefined

  return (
    <div className="dp-login">
      <LoginBackdrop />

      <div className="dp-login-layout">
        <div className="dp-login-intro">
          {/* The card's logo names the product; this one is decoration. */}
          <Logo className="dp-login-brand" alt="" />
          <p className="dp-login-headline">
            <span>Ask Your Data</span>
            <span className="dp-login-headline-accent">Understand Your Business</span>
          </p>
        </div>

        <main className="dp-login-card" aria-labelledby="login-heading">
          <Logo className="dp-login-card-logo" />
          <h1 id="login-heading" className="dp-login-title">
            Welcome back
          </h1>
          <p className="dp-login-subtitle">Sign in to your DataPilot workspace.</p>

          {notice && (
            <p className="dp-login-notice" role="status">
              {notice}
            </p>
          )}

          <form className="dp-login-form" onSubmit={handleSubmit} noValidate aria-busy={submitting}>
            <label htmlFor="demo-username">Username</label>
            <div className="dp-field" data-invalid={invalid}>
              <Icon name="user" size={20} strokeWidth={1.7} />
              <input
                id="demo-username"
                ref={usernameRef}
                name="username"
                type="text"
                placeholder="Enter username"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                value={username}
                onChange={edit(setUsername)}
                disabled={submitting}
                aria-invalid={invalid}
                aria-describedby={describedBy}
              />
            </div>

            <label htmlFor="demo-password">Password</label>
            <div className="dp-field" data-invalid={invalid}>
              <Icon name="lock" size={20} strokeWidth={1.7} />
              <input
                id="demo-password"
                ref={passwordRef}
                name="password"
                type={showPassword ? 'text' : 'password'}
                placeholder="Enter your password"
                autoComplete="current-password"
                autoCapitalize="none"
                spellCheck={false}
                value={password}
                onChange={edit(setPassword)}
                disabled={submitting}
                aria-invalid={invalid}
                aria-describedby={describedBy}
              />
              <button
                type="button"
                className="dp-field-toggle"
                onClick={() => setShowPassword((shown) => !shown)}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                aria-controls="demo-password"
                disabled={submitting}
              >
                <Icon name={showPassword ? 'eyeOff' : 'eye'} size={22} strokeWidth={1.7} />
              </button>
            </div>

            {error && (
              <p id="login-error" className="dp-login-error" role="alert">
                <Icon name="alertCircle" size={18} />
                {error.message}
              </p>
            )}

            <button type="submit" className="dp-button-primary dp-login-submit" disabled={submitting}>
              {submitting && <span className="dp-spinner" aria-hidden="true" />}
              {submitting ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        </main>
      </div>
    </div>
  )
}

/** The approved login background: soft shapes and a faded, tilted dashboard. Decoration only. */
function LoginBackdrop() {
  return (
    <div className="dp-login-backdrop" aria-hidden="true">
      <div className="dp-login-orb dp-login-orb-1" />
      <div className="dp-login-orb dp-login-orb-2" />
      <div className="dp-login-orb dp-login-orb-3" />
      <div className="dp-login-grid" />
      <div className="dp-login-dots dp-login-dots-top" />
      <div className="dp-login-dots dp-login-dots-bottom" />
      <svg className="dp-login-curve" viewBox="0 0 600 260" fill="none" focusable="false">
        <path
          d="M0 250 C60 150 130 120 200 150 C260 175 300 120 360 70 C400 40 430 38 470 52 C520 70 560 40 600 5"
          stroke="#9DB8F5"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
        <circle cx="360" cy="70" r="5.5" fill="#6D9BF5" />
      </svg>
      <div className="dp-login-board">
        <div className="dp-login-board-base" />
        <div className="dp-login-board-panel dp-login-board-line">
          <svg viewBox="0 0 250 190" fill="none" focusable="false">
            <path
              d="M24 150 L58 108 L92 120 L126 84 L160 92 L214 36"
              stroke="#6D8DF0"
              strokeWidth="3"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <path d="M24 150 L58 108 L92 120 L126 84 L160 92 L214 36 L214 160 L24 160Z" fill="#6D8DF0" fillOpacity="0.1" />
            <circle cx="58" cy="108" r="4.5" fill="#6D8DF0" />
            <circle cx="92" cy="120" r="4.5" fill="#6D8DF0" />
            <circle cx="126" cy="84" r="4.5" fill="#6D8DF0" />
            <circle cx="214" cy="36" r="5.5" fill="#6D8DF0" />
          </svg>
        </div>
        <div className="dp-login-board-panel dp-login-board-bars">
          <span />
          <span />
          <span />
          <span />
        </div>
        <div className="dp-login-board-panel dp-login-board-text dp-login-board-text-1">
          <span />
          <span />
          <span />
        </div>
        <div className="dp-login-board-panel dp-login-board-text dp-login-board-text-2">
          <span />
          <span />
        </div>
        <div className="dp-login-board-panel dp-login-board-text dp-login-board-text-3">
          <span />
          <span />
        </div>
        <div className="dp-login-board-donut" />
      </div>
    </div>
  )
}
