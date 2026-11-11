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
    { href: '#/', label: 'System', match: (p) => p.length === 0 },
    { href: '#/cases', label: 'Cases', match: (p) => p[0] === 'cases' },
    auth.can('create') && { href: '#/new', label: 'New case', match: (p) => p[0] === 'new' },
    auth.can('admin') && { href: '#/admin', label: 'Users & roles', match: (p) => p[0] === 'admin' },
  ].filter(Boolean)

  let view
  if (parts[0] === 'cases' && parts[1]) view = <CaseDetail key={parts[1]} caseId={parts[1]} />
  else if (parts[0] === 'cases') view = <CasesList />
  else if (parts[0] === 'new') view = <NewCase />
  else if (parts[0] === 'admin') view = <Admin />
  else view = <Overview />

  return (
    <div className="app">
      <header className="topbar">
        <a href="#/" className="brand">
          <span className="brand-mark" aria-hidden="true">
            <i /><i /><i />
          </span>
          <span>
            <strong>Sentinel</strong>
            <small>Messaging evidence analysis</small>
          </span>
        </a>
        <nav className="nav">
          {nav.map((n) => (
            <a key={n.href} href={n.href} className={n.match(parts) ? 'on' : ''}>
              {n.label}
            </a>
          ))}
        </nav>
        <ConnectionBadge />
        <div className="user-menu">
          <div className="user-id">
            <span className="user-name" title={auth.user?.subject}>{auth.user?.name}</span>
            <span className="role-badges">
              {auth.roles.map((r) => (
                <span key={r} className={`role-badge role-${r}`}>{ROLE_LABEL[r]}</span>
              ))}
            </span>
          </div>
          {!auth.authDisabled && (
            <button type="button" className="btn btn-small" onClick={auth.logout}>
              Sign out
            </button>
          )}
        </div>
      </header>
      {auth.authDisabled && (
        <div className="dev-banner">Development mode: authentication is disabled (no Cognito settings). Never deploy this build.</div>
      )}
      <main className="main">{view}</main>
      <footer className="footer muted small">
        IC-6600 · TEC San Carlos
        {data?.generated_at && <> · coordinator snapshot {new Date(data.generated_at).toLocaleTimeString('en-US')}</>}
      </footer>
    </div>
  )
}
