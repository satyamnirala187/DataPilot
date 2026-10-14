import { WORKSPACE } from '../config/workspace.js'
import Icon from './Icon.jsx'
import Logo from './Logo.jsx'

// The signed-in views, in sidebar order.
const NAV_ITEMS = [
  { view: 'ask', label: 'Ask DataPilot', icon: 'chat' },
  { view: 'history', label: 'History', icon: 'clock' },
  { view: 'saved', label: 'Saved Reports', icon: 'bookmark' },
]

/**
 * The signed-in frame: sidebar (logo, navigation, profile, Log out), top bar and the page's one
 * <main>. It holds no state: App owns the current view and passes the actions in.
 */
export default function AppShell({ view, onNavigate, onAskAnother, onLogout, children }) {
  return (
    <div className="dp-shell">
      <Sidebar view={view} onNavigate={onNavigate} onLogout={onLogout} />
      <div className="dp-shell-body">
        <TopBar onAskAnother={onAskAnother} />
        <main className="dp-shell-main">{children}</main>
      </div>
    </div>
  )
}

function Sidebar({ view, onNavigate, onLogout }) {
  return (
    <div className="dp-sidebar">
      <div className="dp-sidebar-logo">
        <Logo />
      </div>

      <nav className="dp-sidebar-nav" aria-labelledby="dp-nav-label">
        <p id="dp-nav-label" className="dp-sidebar-label">
          Main menu
        </p>
        <ul>
          {NAV_ITEMS.map((item) => {
            const active = item.view === view
            return (
              <li key={item.view}>
                <button
                  type="button"
                  className="dp-nav-item"
                  aria-current={active ? 'page' : undefined}
                  onClick={() => onNavigate(item.view)}
                >
                  <Icon name={item.icon} size={20} strokeWidth={1.7} />
                  {item.label}
                </button>
              </li>
            )
          })}
        </ul>
      </nav>

      <div className="dp-sidebar-footer">
        {/* Display only: one shared demo login, no account to open or edit. */}
        <div className="dp-profile">
          <span className="dp-avatar" aria-hidden="true">
            {WORKSPACE.userInitial}
          </span>
          <div className="dp-profile-text">
            <p className="dp-profile-name">{WORKSPACE.userName}</p>
            <p className="dp-profile-meta">{WORKSPACE.department}</p>
          </div>
        </div>
        <button type="button" className="dp-nav-item dp-logout" onClick={onLogout}>
          <Icon name="logout" size={20} strokeWidth={1.7} />
          Log out
        </button>
      </div>
    </div>
  )
}

function TopBar({ onAskAnother }) {
  return (
    <header className="dp-topbar">
      <button type="button" className="dp-topbar-ask" onClick={onAskAnother}>
        <Icon name="chat" size={18} strokeWidth={1.8} />
        <span>Ask another question…</span>
      </button>
      {/* Read-only label of the fictional demo workspace; not a switcher. */}
      <div className="dp-workspace">
        <p className="dp-workspace-name">{WORKSPACE.workspaceName}</p>
        <p className="dp-workspace-label">{WORKSPACE.workspaceLabel}</p>
      </div>
    </header>
  )
}
