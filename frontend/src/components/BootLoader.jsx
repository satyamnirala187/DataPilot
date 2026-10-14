import Logo from './Logo.jsx'

/**
 * Shown while a stored session is checked, and only until that check resolves: no minimum time.
 * The animated blocks and the progress line are decoration; the status text says what is happening.
 * No aria-busy: on a live region it holds announcements back until it turns false, and this one is
 * removed instead, so the status text might never be read out.
 */
export default function BootLoader() {
  return (
    <div className="dp-access" role="status">
      <div className="dp-boot-mark" aria-hidden="true" />
      <Logo className="dp-boot-logo" />
      <div className="dp-boot-progress" aria-hidden="true">
        <span />
      </div>
      <p className="dp-boot-text">Loading your workspace…</p>
    </div>
  )
}
