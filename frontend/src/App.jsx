import ConnectionBadge from './components/ConnectionBadge.jsx'
import { useHashRoute } from './hooks/useHashRoute.js'
import { useLiveState } from './hooks/useLiveState.jsx'
import CaseDetail from './views/CaseDetail.jsx'
import CasesList from './views/CasesList.jsx'
import NewCase from './views/NewCase.jsx'
import Overview from './views/Overview.jsx'

const NAV = [
  { href: '#/', label: 'System', match: (p) => p.length === 0 },
  { href: '#/cases', label: 'Cases', match: (p) => p[0] === 'cases' },
  { href: '#/new', label: 'New case', match: (p) => p[0] === 'new' },
]

export default function App() {
  const { parts } = useHashRoute()
  const { data } = useLiveState()

  let view
  if (parts[0] === 'cases' && parts[1]) view = <CaseDetail key={parts[1]} caseId={parts[1]} />
  else if (parts[0] === 'cases') view = <CasesList />
  else if (parts[0] === 'new') view = <NewCase />
  else view = <Overview />

  return (
    <div className="app">
      <header className="topbar">
        <a href="#/" className="brand">
          <span className="brand-mark" aria-hidden="true">
            <i /><i /><i /><i />
          </span>
          <span>
            <strong>DMP Dashboard</strong>
            <small>Case-based multimedia processing</small>
          </span>
        </a>
        <nav className="nav">
          {NAV.map((n) => (
            <a key={n.href} href={n.href} className={n.match(parts) ? 'on' : ''}>
              {n.label}
            </a>
          ))}
        </nav>
        <ConnectionBadge />
      </header>
      <main className="main">{view}</main>
      <footer className="footer muted small">
        IC-6600 · TEC San Carlos
        {data?.generated_at && <> · coordinator snapshot {new Date(data.generated_at).toLocaleTimeString('en-US')}</>}
      </footer>
    </div>
  )
}
