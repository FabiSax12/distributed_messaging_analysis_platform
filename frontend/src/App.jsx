import { useAuth } from './auth/AuthProvider.jsx'
import AuthScreen from './auth/AuthScreen.jsx'
import ConnectionBadge from './components/ConnectionBadge.jsx'
import { useHashRoute } from './hooks/useHashRoute.js'
import { LiveStateProvider, useLiveState } from './hooks/useLiveState.jsx'
import Admin from './views/Admin.jsx'
import CaseDetail from './views/CaseDetail.jsx'
import CasesList from './views/CasesList.jsx'
import NewCase from './views/NewCase.jsx'
import Overview from './views/Overview.jsx'

const ROLE_LABEL = { analyst: 'Analyst', supervisor: 'Supervisor', administrator: 'Administrator' }

// Session boundary: nothing below talks to the API until there is a session
// with a recognized role. The live-state socket/polling only exists while
// signed in, so signing out stops it.
export default function Root() {
  const auth = useAuth()
  if (auth.status !== 'signed-in') return <AuthScreen />
  return (
    <LiveStateProvider token={auth.accessToken} onAuthClose={auth.refresh}>
      <App />
    </LiveStateProvider>
  )
}

function App() {
  const { parts } = useHashRoute()
  const { data } = useLiveState()
  const auth = useAuth()

  const nav = [
    { group: 'Monitor', href: '#/', label: 'Operations', icon: 'pulse', match: (p) => p.length === 0 },
    { group: 'Evidence', href: '#/cases', label: 'Case board', icon: 'folder', match: (p) => p[0] === 'cases' },
    auth.can('create') && { group: 'Evidence', href: '#/new', label: 'Open a case', icon: 'plus', match: (p) => p[0] === 'new' },
    auth.can('admin') && { group: 'Access', href: '#/admin', label: 'Users & roles', icon: 'users', match: (p) => p[0] === 'admin' },
  ].filter(Boolean)
  const groups = [...new Set(nav.map((n) => n.group))]

  let view
  if (parts[0] === 'cases' && parts[1]) view = <CaseDetail key={parts[1]} caseId={parts[1]} />
  else if (parts[0] === 'cases') view = <CasesList />
  else if (parts[0] === 'new') view = <NewCase />
  else if (parts[0] === 'admin') view = <Admin />
  else view = <Overview />

  return (
    <div className="shell">
      <aside className="sidebar">
        <a href="#/" className="brand">
          <span className="brand-mark" aria-hidden="true">
            <i /><i /><i />
          </span>
          <span>
            <strong>Sentinel</strong>
            <small>Messaging evidence analysis</small>
          </span>
        </a>
        <nav className="side-nav" aria-label="Main">
          {groups.map((g) => (
            <div key={g} className="side-group">
              <span className="side-group-label">{g}</span>
              {nav.filter((n) => n.group === g).map((n) => (
                <a key={n.href} href={n.href} className={n.match(parts) ? 'on' : ''}>
                  <Icon name={n.icon} />
                  {n.label}
                </a>
              ))}
            </div>
          ))}
        </nav>
        <div className="side-foot">
          <ConnectionBadge />
          <div className="user-card">
            <span className="user-avatar" aria-hidden="true">{(auth.user?.name || '?').slice(0, 1).toUpperCase()}</span>
            <div className="user-id">
              <span className="user-name" title={auth.user?.subject}>{auth.user?.name}</span>
              <span className="role-badges">
                {auth.roles.map((r) => (
                  <span key={r} className={`role-badge role-${r}`}>{ROLE_LABEL[r]}</span>
                ))}
              </span>
            </div>
          </div>
          {!auth.authDisabled && (
            <button type="button" className="btn btn-small side-signout" onClick={auth.logout}>
              Sign out
            </button>
          )}
        </div>
      </aside>
      <div className="workspace">
        {auth.authDisabled && (
          <div className="dev-banner">Development mode: authentication is disabled (no Cognito settings). Never deploy this build.</div>
        )}
        <main className="main">{view}</main>
        <footer className="footer muted small">
          IC-6600 · TEC San Carlos
          {data?.generated_at && <> · coordinator snapshot {new Date(data.generated_at).toLocaleTimeString('en-US')}</>}
        </footer>
      </div>
    </div>
  )
}

const ICONS = {
  pulse: 'M3 12h4l2-6 4 12 2-6h6',
  folder: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  plus: 'M12 5v14M5 12h14',
  users: 'M16 19v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1M9 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6M22 19v-1a4 4 0 0 0-3-3.9M16 4.1a3 3 0 0 1 0 5.8',
}

function Icon({ name }) {
  return (
    <svg className="side-icon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <path d={ICONS[name]} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
