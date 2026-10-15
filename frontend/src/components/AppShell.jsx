import { WORKSPACE } from '../config/workspace.js'
import Icon from './Icon.jsx'
import Logo from './Logo.jsx'

// The approved DataPilot symbol (the favicon), shown in place of the full logo when the sidebar is
// a narrow icon rail. Same picture, same alt text: one image either way.
const SYMBOL_URL = `${import.meta.env.BASE_URL}favicon.svg`
// Keep in step with the rail breakpoint in index.css (UI V2: responsive layout).
const RAIL_MEDIA = '(max-width: 1099px)'

// The signed-in views, in sidebar order.
const NAV_ITEMS = [
  { view: 'ask', label: 'Ask DataPilot', icon: 'chat' },
  { view: 'history', label: 'History', icon: 'clock' },
  { view: 'saved', label: 'Saved Reports', icon: 'bookmark' },
]

/**
 * The signed-in frame: sidebar (logo, navigation, profile, Log out), top bar and the page's one
 * <main>. It holds no state: App owns the current view and passes the actions in.
 * savedBadge: the Saved Reports count to show ("3", "50+"), or null for none.
 */
export default function AppShell({ view, onNavigate, onAskAnother, showAskAnother = true, onLogout, savedBadge = null, children }) {
  return (
    <div className="dp-shell">
      <Sidebar view={view} onNavigate={onNavigate} onLogout={onLogout} savedBadge={savedBadge} />
      <div className="dp-shell-body">
        <TopBar onAskAnother={onAskAnother} showAskAnother={showAskAnother} />
        <main className="dp-shell-main">{children}</main>
      </div>
    </div>
  )
}

function Sidebar({ view, onNavigate, onLogout, savedBadge }) {
  return (
    <div className="dp-sidebar">
      <div className="dp-sidebar-logo">
        <picture>
          <source media={RAIL_MEDIA} srcSet={SYMBOL_URL} />
          <Logo />
        </picture>
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
                {/* In the icon rail the label is hidden visually but still names the button; the
                    title gives mouse users the same name. */}
                <button
                  type="button"
                  className="dp-nav-item"
                  aria-current={active ? 'page' : undefined}
                  title={item.label}
                  onClick={() => onNavigate(item.view)}
                >
                  <Icon name={item.icon} size={20} strokeWidth={1.7} />
                  <span className="dp-nav-label">{item.label}</span>
                  {/* Visual only, so the button is still announced as "Saved Reports". */}
                  {item.view === 'saved' && savedBadge && (
                    <span className="dp-nav-badge" aria-hidden="true">
                      {savedBadge}
                    </span>
                  )}
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
        <button type="button" className="dp-nav-item dp-logout" title="Log out" onClick={onLogout}>
          <Icon name="logout" size={20} strokeWidth={1.7} />
          <span className="dp-nav-label">Log out</span>
        </button>
      </div>
    </div>
  )
}

function TopBar({ onAskAnother, showAskAnother }) {
  return (
    <header className="dp-topbar">
      {/* Not shown on the empty Ask page, as in the approved design; the space stays so the
          workspace label keeps its place. */}
      {showAskAnother ? (
        <button type="button" className="dp-topbar-ask" onClick={onAskAnother}>
          <Icon name="chat" size={18} strokeWidth={1.8} />
          <span>Ask another question…</span>
        </button>
      ) : (
        <span className="dp-topbar-spacer" />
      )}
      {/* Read-only label of the fictional demo workspace; not a switcher. */}
      <div className="dp-workspace">
        <p className="dp-workspace-name">{WORKSPACE.workspaceName}</p>
        <p className="dp-workspace-label">{WORKSPACE.workspaceLabel}</p>
      </div>
    </header>
  )
}
