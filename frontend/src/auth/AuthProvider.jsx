// Session boundary of the dashboard: who is signed in, with which roles.
//
// status:
//   'loading'       resolving a callback, refreshing a stored session or reading /api/me
//   'signed-out'    no session (login screen); `notice` explains why (expired, signed out...)
//   'signed-in'     access token in memory, roles known
//   'denied'        valid token but no recognized role (analyst/supervisor/administrator)
//   'error'         backend or Cognito unavailable; retry possible
//   'misconfigured' production build without Cognito settings (fails closed)
//
// The access token (never the ID token) is handed to the REST client and the
// WebSocket. It is refreshed 2 minutes before it expires; a failed refresh ends
// the session. If /api/me fails (other than 401) after a background refresh,
// the current identity is kept with the new token and /api/me is read again
// on the next refresh.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError, configureAuth } from '../api/client.js'
import {
  AUTH_CONFIGURED,
  AUTH_DISABLED,
  bootstrapTokens,
  decodeJwt,
  logout as cognitoLogout,
  refreshTokens,
  startLogin,
} from './cognito.js'

export const ROLES = ['analyst', 'supervisor', 'administrator']
// Mirrors coordinator/auth.py `authorize`: the server enforces it; the UI only
// hides what would be denied.
const ACTIONS = {
  read: ROLES,
  upload: ROLES,
  create: ROLES,
  cancel: ['supervisor', 'administrator'],
  admin: ['administrator'],
}
const REFRESH_MARGIN_S = 120

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [session, setSession] = useState({ status: 'loading' })
  const tokenRef = useRef(null)
  const refreshTimer = useRef(null)
  const refreshing = useRef(null)
  const refreshRef = useRef(null) // latest refreshNow, for the timer
  // Bumped on logout: work started before it (a refresh, /api/me) is discarded.
  const generationRef = useRef(0)

  const endSession = useCallback((notice) => {
    clearTimeout(refreshTimer.current)
    tokenRef.current = null
    setSession({ status: 'signed-out', notice })
  }, [])

  const loadIdentity = useCallback(
    async (tokens) => {
      const generation = generationRef.current
      try {
        const me = await api.me()
        if (generation !== generationRef.current) return // signed out meanwhile
        const roles = (me.roles || []).filter((r) => ROLES.includes(r))
        const id = tokens?.id_token ? decodeJwt(tokens.id_token) : null
        const access = tokens?.access_token ? decodeJwt(tokens.access_token) : null
        setSession({
          status: roles.length ? 'signed-in' : 'denied',
          accessToken: tokens?.access_token ?? null,
          expiresAt: access?.exp ? access.exp * 1000 : null,
          user: {
            subject: me.subject,
            name: id?.email || id?.['cognito:username'] || access?.username || me.subject,
          },
          roles,
        })
      } catch (err) {
        if (generation !== generationRef.current) return
        if (err instanceof ApiError && err.status === 401) {
          endSession('The coordinator did not accept your session. Sign in again.')
          return
        }
        // Only the initial sign-in may fail into 'error'; a background refresh
        // keeps the working session and its views mounted.
        console.warn('Could not read /api/me; a signed-in session keeps its current roles.', err)
        setSession((current) => {
          if (current.status !== 'signed-in' && current.status !== 'denied') return { status: 'error', error: err }
          const access = tokens?.access_token ? decodeJwt(tokens.access_token) : null
          return {
            ...current,
            accessToken: tokens?.access_token ?? current.accessToken,
            expiresAt: access?.exp ? access.exp * 1000 : current.expiresAt,
          }
        })
      }
    },
    [endSession],
  )

  const applyTokens = useCallback(
    async (tokens) => {
      tokenRef.current = tokens.access_token
      const exp = decodeJwt(tokens.access_token)?.exp
      clearTimeout(refreshTimer.current)
      if (exp) {
        const delay = Math.max(5, exp - Date.now() / 1000 - REFRESH_MARGIN_S) * 1000
        refreshTimer.current = setTimeout(() => refreshRef.current?.(), delay)
      }
      await loadIdentity(tokens)
    },
    [loadIdentity],
  )

  // Single-flight refresh, shared by the timer, the REST client (on 401) and
  // the WebSocket (closed by the server on expiry).
  const refreshNow = useCallback(async () => {
    if (AUTH_DISABLED) return null
    if (!refreshing.current) {
      const generation = generationRef.current
      const flight = (async () => {
        try {
          const tokens = await refreshTokens()
          if (generation !== generationRef.current) return null // signed out meanwhile
          await applyTokens(tokens)
          return generation === generationRef.current ? tokens.access_token : null
        } catch {
          if (generation === generationRef.current) endSession('Your session expired. Sign in again.')
          return null
        } finally {
          if (refreshing.current === flight) refreshing.current = null
        }
      })()
      refreshing.current = flight
    }
    return refreshing.current
  }, [applyTokens, endSession])

  refreshRef.current = refreshNow

  useEffect(() => {
    configureAuth({ getToken: () => tokenRef.current, refresh: refreshNow })
  }, [refreshNow])

  const start = useCallback(async (retry = false) => {
    setSession({ status: 'loading' })
    if (AUTH_DISABLED) {
      await loadIdentity(null)
      return
    }
    if (!AUTH_CONFIGURED) {
      setSession({ status: 'misconfigured' })
      return
    }
    try {
      const tokens = await bootstrapTokens({ reset: retry })
      if (tokens) await applyTokens(tokens)
      else setSession({ status: 'signed-out' })
    } catch (err) {
      endSession(err.message)
    }
  }, [applyTokens, endSession, loadIdentity])

  useEffect(() => {
    start()
    return () => clearTimeout(refreshTimer.current)
  }, [start])

  const value = useMemo(() => {
    const roles = session.roles || []
    return {
      ...session,
      authDisabled: AUTH_DISABLED,
      can: (action) => (ACTIONS[action] || []).some((r) => roles.includes(r)),
      login: () => startLogin(window.location.hash || '#/'),
      logout: async () => {
        generationRef.current += 1
        refreshing.current = null
        clearTimeout(refreshTimer.current)
        tokenRef.current = null
        if (AUTH_DISABLED) return
        setSession({ status: 'loading' })
        await cognitoLogout()
      },
      refresh: refreshNow,
      retry: () => start(true),
    }
  }, [session, refreshNow, start])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
