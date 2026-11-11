// Cognito Hosted UI: authorization code + PKCE for a public SPA client (no
// client secret). Web Crypto only, no auth library.
//
// Token storage:
// - access and ID tokens: memory only (lost on reload, refreshed from the
//   refresh token);
// - refresh token: sessionStorage (this tab only, cleared when the tab
//   closes; never localStorage);
// - PKCE verifier + OAuth state: sessionStorage for the duration of the
//   redirect, removed on the callback.
// Tokens never go into URLs or console output.

export const COGNITO_DOMAIN = (import.meta.env.VITE_COGNITO_DOMAIN || '').replace(/\/$/, '')
export const COGNITO_CLIENT_ID = import.meta.env.VITE_COGNITO_CLIENT_ID || ''
// Without Cognito settings, auth is off only in development (a local
// coordinator in unauthenticated mode) or when explicitly disabled. A
// production build without them fails closed (see AuthProvider).
export const AUTH_CONFIGURED = Boolean(COGNITO_DOMAIN && COGNITO_CLIENT_ID)
export const AUTH_DISABLED = !AUTH_CONFIGURED && (import.meta.env.DEV || import.meta.env.VITE_AUTH_DISABLED === 'true')

const SCOPES = 'openid email profile'
const PENDING_KEY = 'madp.oauth.pending'
const REFRESH_KEY = 'madp.oauth.refresh'

// Must match a Cognito callback/sign-out URL exactly: the site origin, no path
// (Terraform registers http://localhost:5173 and the CloudFront URL).
export function redirectUri() {
  return window.location.origin
}

function base64url(bytes) {
  let s = ''
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b)
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function randomString(bytes = 32) {
  return base64url(crypto.getRandomValues(new Uint8Array(bytes)))
}

export function decodeJwt(token) {
  try {
    const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    const json = decodeURIComponent(
      atob(part.padEnd(part.length + ((4 - (part.length % 4)) % 4), '='))
        .split('')
        .map((c) => '%' + c.charCodeAt(0).toString(16).padStart(2, '0'))
        .join(''),
    )
    return JSON.parse(json)
  } catch {
    return null
  }
}

export async function startLogin(returnHash = window.location.hash) {
  const verifier = randomString(48)
  const state = randomString(24)
  const challenge = base64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)))
  sessionStorage.setItem(PENDING_KEY, JSON.stringify({ verifier, state, returnHash, at: Date.now() }))
  const params = new URLSearchParams({
    response_type: 'code',
    client_id: COGNITO_CLIENT_ID,
    redirect_uri: redirectUri(),
    scope: SCOPES,
    state,
    code_challenge: challenge,
    code_challenge_method: 'S256',
  })
  window.location.assign(`${COGNITO_DOMAIN}/oauth2/authorize?${params}`)
}

export class AuthError extends Error {}

async function tokenRequest(body) {
  let res
  try {
    res = await fetch(`${COGNITO_DOMAIN}/oauth2/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ client_id: COGNITO_CLIENT_ID, ...body }),
    })
  } catch {
    throw new AuthError('Could not reach Cognito to complete sign-in.')
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const err = new AuthError(data.error === 'invalid_grant' ? 'The sign-in session expired.' : `Cognito rejected the token request (${data.error || res.status}).`)
    err.code = data.error
    throw err
  }
  return data
}

// True when the current URL is an OAuth callback (?code=... or ?error=...).
export function isCallback() {
  const q = new URLSearchParams(window.location.search)
  return q.has('code') || q.has('error')
}

// Exchanges the code for tokens and removes the query from the address bar
// before anything else reads it. Returns the token set.
export async function completeLogin() {
  const q = new URLSearchParams(window.location.search)
  const pendingRaw = sessionStorage.getItem(PENDING_KEY)
  sessionStorage.removeItem(PENDING_KEY)
  const pending = pendingRaw ? JSON.parse(pendingRaw) : null
  // Clean the URL first: the code must not stay in history or be re-used.
  window.history.replaceState(null, '', `${window.location.pathname}${pending?.returnHash || '#/'}`)

  if (q.has('error')) throw new AuthError(`Sign-in was not completed: ${q.get('error_description') || q.get('error')}.`)
  if (!pending || !q.get('state') || q.get('state') !== pending.state) {
    throw new AuthError('Sign-in response did not match this browser session (OAuth state check failed). Try again.')
  }
  const tokens = await tokenRequest({
    grant_type: 'authorization_code',
    code: q.get('code'),
    redirect_uri: redirectUri(),
    code_verifier: pending.verifier,
  })
  if (tokens.refresh_token) sessionStorage.setItem(REFRESH_KEY, tokens.refresh_token)
  return tokens
}

export function storedRefreshToken() {
  return sessionStorage.getItem(REFRESH_KEY)
}

export async function refreshTokens() {
  const refresh = storedRefreshToken()
  if (!refresh) throw new AuthError('No session to refresh.')
  try {
    return await tokenRequest({ grant_type: 'refresh_token', refresh_token: refresh })
  } catch (err) {
    if (err.code === 'invalid_grant') sessionStorage.removeItem(REFRESH_KEY)
    throw err
  }
}

export async function logout() {
  const refresh = storedRefreshToken()
  sessionStorage.removeItem(REFRESH_KEY)
  sessionStorage.removeItem(PENDING_KEY)
  if (refresh) {
    // Revocation also invalidates access tokens issued from this refresh token.
    await fetch(`${COGNITO_DOMAIN}/oauth2/revoke`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ token: refresh, client_id: COGNITO_CLIENT_ID }),
    }).catch(() => {})
  }
  const params = new URLSearchParams({ client_id: COGNITO_CLIENT_ID, logout_uri: redirectUri() })
  window.location.assign(`${COGNITO_DOMAIN}/logout?${params}`)
}

// Startup: resolve the callback or the stored session once per page load.
// Single-flight because React StrictMode runs effects twice in development
// and an authorization code can only be redeemed once.
let bootstrap = null
export function bootstrapTokens({ reset = false } = {}) {
  if (reset) bootstrap = null
  if (!bootstrap) {
    bootstrap = isCallback() ? completeLogin() : storedRefreshToken() ? refreshTokens() : Promise.resolve(null)
  }
  return bootstrap
}
