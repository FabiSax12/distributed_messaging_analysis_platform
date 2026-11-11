// Administrator view: Cognito users and their dashboard groups.
//
// GET /api/admin/users?cursor=... -> {users: [{username, enabled, status, roles}], cursor}
// POST /api/admin/roles {username, role, enabled} adds (enabled=true) or
// removes the user from that group. It does not enable/disable the Cognito
// account, and creating/inviting users stays a manual Cognito task. A changed
// group reaches the user's next token (after refresh or a new sign-in).

import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client.js'
import { ROLES, useAuth } from '../auth/AuthProvider.jsx'
import { Empty, ErrorBanner, Section } from '../components/ui.jsx'

const ROLE_HELP = {
  analyst: 'Reads cases, uploads files, creates cases',
  supervisor: 'Analyst actions plus cancelling cases',
  administrator: 'Supervisor actions plus managing roles',
}

export default function Admin() {
  const auth = useAuth()
  const [users, setUsers] = useState([])
  const [cursor, setCursor] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [pending, setPending] = useState({}) // "user|role" -> true while saving
  const [notice, setNotice] = useState(null)

  const load = useCallback(async (next = null) => {
    setLoading(true)
    setError(null)
    try {
      const page = await api.adminUsers(next)
      setUsers((prev) => (next ? [...prev, ...page.users] : page.users))
      setCursor(page.cursor || null)
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (auth.can('admin')) load()
  }, [auth, load])

  if (!auth.can('admin')) {
    return <Empty title="Administrators only">Your role does not include user management.</Empty>
  }

  const toggle = async (user, role, enabled) => {
    const key = `${user.username}|${role}`
    setPending((p) => ({ ...p, [key]: true }))
    setError(null)
    setNotice(null)
    try {
      await api.setRole(user.username, role, enabled)
      setUsers((list) =>
        list.map((u) =>
          u.username === user.username
            ? { ...u, roles: enabled ? [...new Set([...u.roles, role])] : u.roles.filter((r) => r !== role) }
            : u,
        ),
      )
      setNotice(`${enabled ? 'Added' : 'Removed'} ${role} ${enabled ? 'to' : 'from'} ${user.username}. It applies to their next token.`)
    } catch (err) {
      setError(err)
    } finally {
      setPending((p) => {
        const { [key]: _, ...rest } = p
        return rest
      })
    }
  }

  return (
    <div className="stack-lg">
      <div className="page-head">
        <h1>Users &amp; roles</h1>
        <p className="muted">
          Group membership in the Cognito user pool. Creating or inviting users is done in Cognito by the operator.
        </p>
      </div>
      <ErrorBanner error={error} onRetry={() => load()} />
      {notice && <p className="auth-notice" role="status">{notice}</p>}
      <Section title="Users" aside={<span className="muted">{users.length} loaded</span>}>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>User</th>
                <th>Account</th>
                {ROLES.map((r) => (
                  <th key={r} className="center" title={ROLE_HELP[r]}>{r}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.username}>
                  <td className="mono small">{u.username}{u.username === auth.user?.subject && <span className="muted"> (you)</span>}</td>
                  <td className="small">
                    <span className={`pill tone-${u.enabled ? 'ok' : 'bad'}`}>{u.enabled ? 'Enabled' : 'Disabled'}</span>{' '}
                    <span className="muted">{u.status}</span>
                  </td>
                  {ROLES.map((r) => {
                    const key = `${u.username}|${r}`
                    const checked = u.roles.includes(r)
                    return (
                      <td key={r} className="center">
                        <input
                          type="checkbox"
                          aria-label={`${r} for ${u.username}`}
                          checked={checked}
                          disabled={Boolean(pending[key])}
                          onChange={(e) => toggle(u, r, e.target.checked)}
                        />
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!users.length && !loading && !error && <p className="muted">No users returned.</p>}
        <div className="row-gap">
          {cursor && (
            <button type="button" className="btn" onClick={() => load(cursor)} disabled={loading}>
              {loading ? 'Loading…' : 'Load more'}
            </button>
          )}
          {loading && !cursor && <span className="muted">Loading…</span>}
        </div>
      </Section>
      <Section title="What each role can do">
        <ul className="role-help">
          {ROLES.map((r) => (
            <li key={r}><span className={`role-badge role-${r}`}>{r}</span> {ROLE_HELP[r]}</li>
          ))}
        </ul>
      </Section>
    </div>
  )
}
