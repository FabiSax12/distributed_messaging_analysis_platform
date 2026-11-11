// Live system state (StateResponse), shared by all views.
//
// Primary source: WebSocket /api/ws, which pushes {"type":"state","data":...}
// whenever the coordinator snapshot changes, and {"type":"ping"} every 25 s.
// Fallback: while the socket is not open, polling of GET /api/state every
// 2 s. Reconnect with exponential backoff 1 s → 2 s → 4 s … up to 30 s.
//
// Authentication: the socket offers the subprotocols ['bearer', accessToken];
// the coordinator selects 'bearer', validates before accepting and closes with
// 1008 when the token is invalid or expires. On 1008 we ask AuthProvider for a
// refresh instead of retrying with the same token; a new token reconnects. If
// the socket closes with 1008 again before it has stayed open for
// WS_STABLE_MS, the cause is not the token (e.g. the origin check), so we stop
// reconnecting and keep polling until the user presses Reconnect. The
// provider is only mounted while signed in, so signing out closes the socket
// and stops polling.

import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api } from '../api/client.js'
import {
  POLL_INTERVAL_MS,
  WS_BACKOFF_MAX_MS,
  WS_BACKOFF_START_MS,
  WS_SILENCE_TIMEOUT_MS,
  WS_STABLE_MS,
  WS_URL,
} from '../config.js'

const LiveStateContext = createContext(null)

export function LiveStateProvider({ children, token = null, onAuthClose }) {
  const [data, setData] = useState(null)
  // 'connecting' | 'ws' | 'polling' | 'offline'
  const [mode, setMode] = useState('connecting')
  const [receivedAt, setReceivedAt] = useState(null)
  const [nextRetryAt, setNextRetryAt] = useState(null)
  const [lastError, setLastError] = useState(null)

  const wsRef = useRef(null)
  const backoffRef = useRef(WS_BACKOFF_START_MS)
  const retryTimerRef = useRef(null)
  const silenceTimerRef = useRef(null)
  const pollTimerRef = useRef(null)
  const unmountedRef = useRef(false)
  // Set when a 1008 close asked for a refresh; cleared once a socket stays
  // open for WS_STABLE_MS. Refs survive the remount caused by the new token.
  const authRefreshPendingRef = useRef(false)
  const stableTimerRef = useRef(null)
  // Why live updates stopped after a repeated 1008; polling keeps showing it.
  const stoppedReasonRef = useRef(null)

  const accept = useCallback((state) => {
    setData(state)
    setReceivedAt(new Date())
  }, [])

  // ---- Fallback polling ------------------------------------------------ //

  const pollOnce = useCallback(async () => {
    try {
      const state = await api.state()
      if (unmountedRef.current) return
      accept(state)
      setLastError(stoppedReasonRef.current)
      if (wsRef.current?.readyState !== WebSocket.OPEN) setMode('polling')
    } catch (err) {
      if (unmountedRef.current) return
      setLastError(err.message)
      if (wsRef.current?.readyState !== WebSocket.OPEN) setMode('offline')
    }
  }, [accept])

  const startPolling = useCallback(() => {
    if (pollTimerRef.current) return
    pollOnce()
    pollTimerRef.current = setInterval(pollOnce, POLL_INTERVAL_MS)
  }, [pollOnce])

  const stopPolling = useCallback(() => {
    clearInterval(pollTimerRef.current)
    pollTimerRef.current = null
  }, [])

  // ---- WebSocket ---------------------------------------------------------- //

  const armSilenceTimer = useCallback(() => {
    clearTimeout(silenceTimerRef.current)
    silenceTimerRef.current = setTimeout(() => {
      // No state or ping in 60 s: the socket is dead even if it has not closed.
      wsRef.current?.close()
    }, WS_SILENCE_TIMEOUT_MS)
  }, [])

  const connect = useCallback(() => {
    if (unmountedRef.current) return
    clearTimeout(retryTimerRef.current)
    setNextRetryAt(null)

    let ws
    try {
      ws = token ? new WebSocket(WS_URL, ['bearer', token]) : new WebSocket(WS_URL)
    } catch (err) {
      setLastError(err.message)
      return
    }
    wsRef.current = ws

    ws.onopen = () => {
      backoffRef.current = WS_BACKOFF_START_MS
      setMode('ws')
      stoppedReasonRef.current = null
      setLastError(null)
      stopPolling()
      armSilenceTimer()
      clearTimeout(stableTimerRef.current)
      stableTimerRef.current = setTimeout(() => {
        authRefreshPendingRef.current = false
      }, WS_STABLE_MS)
    }

    ws.onmessage = (event) => {
      armSilenceTimer()
      let msg
      try {
        msg = JSON.parse(event.data)
      } catch {
        return
      }
      if (msg.type === 'state') accept(msg.data)
      // type === 'ping': only keeps the connection alive, carries no data.
    }

    ws.onerror = () => {
      // The real detail arrives in onclose; the browser does not expose the reason.
    }

    ws.onclose = (event) => {
      if (wsRef.current !== ws || unmountedRef.current) return
      clearTimeout(silenceTimerRef.current)
      clearTimeout(stableTimerRef.current)
      wsRef.current = null
      if (event.code === 1008) {
        // Origin check or token check failed. With a token, refresh it once:
        // the new token remounts this effect and reconnects. Polling keeps the
        // view alive meanwhile (and is itself refreshed on 401).
        setLastError('The coordinator closed the live connection (session expired or origin not verified).')
        if (token && onAuthClose) {
          setMode('polling')
          startPolling()
          if (authRefreshPendingRef.current) {
            // Rejected again right after a refresh: a new token will not help.
            setNextRetryAt(null)
            stoppedReasonRef.current =
              'The coordinator rejected the live connection again after a token refresh (origin not verified?). Live updates stopped; polling continues. Press Reconnect to retry.'
            setLastError(stoppedReasonRef.current)
            return
          }
          authRefreshPendingRef.current = true
          onAuthClose()
          return
        }
      } else if (event.code === 1013) setLastError('The coordinator reached its maximum number of WebSocket clients.')

      setMode((m) => (m === 'offline' ? 'offline' : 'polling'))
      startPolling()

      const delay = backoffRef.current
      backoffRef.current = Math.min(delay * 2, WS_BACKOFF_MAX_MS)
      setNextRetryAt(new Date(Date.now() + delay))
      retryTimerRef.current = setTimeout(connect, delay)
    }
  }, [accept, armSilenceTimer, startPolling, stopPolling, token, onAuthClose])

  const reconnectNow = useCallback(() => {
    backoffRef.current = WS_BACKOFF_START_MS
    authRefreshPendingRef.current = false
    stoppedReasonRef.current = null
    const ws = wsRef.current
    wsRef.current = null
    ws?.close()
    connect()
  }, [connect])

  useEffect(() => {
    unmountedRef.current = false
    // Start with polling to get data as soon as possible; it turns itself off
    // as soon as the socket opens (the coordinator sends the latest state on connect).
    startPolling()
    connect()
    return () => {
      unmountedRef.current = true
      clearTimeout(retryTimerRef.current)
      clearTimeout(silenceTimerRef.current)
      clearTimeout(stableTimerRef.current)
      stopPolling()
      const ws = wsRef.current
      wsRef.current = null
      ws?.close()
    }
  }, [connect, startPolling, stopPolling])

  const value = { data, mode, receivedAt, nextRetryAt, lastError, reconnectNow, refresh: pollOnce }
  return <LiveStateContext.Provider value={value}>{children}</LiveStateContext.Provider>
}

export function useLiveState() {
  const ctx = useContext(LiveStateContext)
  if (!ctx) throw new Error('useLiveState must be used inside <LiveStateProvider>')
  return ctx
}
