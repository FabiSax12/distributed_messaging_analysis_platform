// Everything shown without a usable session: loading, sign-in, access denied,
// backend/Cognito errors and a build without Cognito settings.

import { useAuth } from './AuthProvider.jsx'

export default function AuthScreen() {
  const auth = useAuth()

  let body
  switch (auth.status) {
    case 'loading':
      body = (
        <>
          <h1>Checking your session…</h1>
          <p className="muted">Contacting Cognito and the coordinator.</p>
        </>
      )
      break
    case 'denied':
      body = (
        <>
          <h1>No access</h1>
          <p>
            You are signed in as <b>{auth.user?.name}</b>, but your account is not in any dashboard group
            (analyst, supervisor or administrator).
          </p>
          <p className="muted small">
            Ask an administrator to add you to a group, then sign out and in again: group changes only reach new tokens.
          </p>
          <button type="button" className="btn" onClick={auth.logout}>Sign out</button>
        </>
      )
      break
    case 'error':
      body = (
        <>
          <h1>The platform is not reachable</h1>
          <p>{auth.error?.message || 'The coordinator did not answer.'}</p>
          <p className="muted small">
            Your session is fine; the backend or its configuration is unavailable. This is not a sign-in problem.
          </p>
          <div className="row-gap">
            <button type="button" className="btn btn-primary" onClick={auth.retry}>Try again</button>
            <button type="button" className="btn" onClick={auth.logout}>Sign out</button>
          </div>
        </>
      )
      break
    case 'misconfigured':
      body = (
        <>
          <h1>Sign-in is not configured</h1>
          <p>
            This build has no Cognito settings (<code>VITE_COGNITO_DOMAIN</code>, <code>VITE_COGNITO_CLIENT_ID</code>), so it
            refuses to run without authentication.
          </p>
        </>
      )
      break
    default:
      body = (
        <>
          <h1>Sign in</h1>
          <p className="muted">
            Analyze WhatsApp and Telegram exports, images and voice notes. Access is limited to analysts, supervisors and
            administrators.
          </p>
          {auth.notice && <p className="auth-notice" role="status">{auth.notice}</p>}
          <button type="button" className="btn btn-primary btn-wide" onClick={auth.login}>
            Continue with Cognito
          </button>
          <p className="muted small">You will be redirected to the institution sign-in page.</p>
        </>
      )
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="brand auth-brand">
          <span className="brand-mark" aria-hidden="true">
            <i /><i /><i />
          </span>
          <span>
            <strong>Sentinel</strong>
            <small>Messaging evidence analysis</small>
          </span>
        </div>
        {body}
      </div>
      <p className="muted small auth-foot">IC-6600 · TEC San Carlos</p>
    </div>
  )
}
