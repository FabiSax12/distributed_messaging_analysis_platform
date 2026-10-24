// Live system state (StateResponse), shared by all views.
//
// Primary source: WebSocket /api/ws, which pushes {"type":"state","data":...}
// whenever the coordinator snapshot changes, and {"type":"ping"} every 25 s.
// Fallback: while the socket is not open, polling of GET /api/state every
// 2 s. Reconnect with exponential backoff 1 s → 2 s → 4 s … up to 30 s.

import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api } from '../api/client.js'
import {
  POLL_INTERVAL_MS,
  WS_BACKOFF_MAX_MS,
  WS_BACKOFF_START_MS,
  WS_SILENCE_TIMEOUT_MS,
  WS_URL,
} from '../config.js'

const LiveStateContext = createContext(null)

export function LiveStateProvider({ children }) {
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
      setLastError(null)
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
      ws = new WebSocket(WS_URL)
    } catch (err) {
      setLastError(err.message)
      return
    }
    wsRef.current = ws

    ws.onopen = () => {
      backoffRef.current = WS_BACKOFF_START_MS
      setMode('ws')
      setLastError(null)
      stopPolling()
      armSilenceTimer()
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
      clearTimeout(silenceTimerRef.current)
      if (wsRef.current !== ws || unmountedRef.current) return
      wsRef.current = null
      if (event.code === 1008) setLastError('The coordinator rejected the WebSocket (unverified origin).')
      else if (event.code === 1013) setLastError('The coordinator reached its maximum number of WebSocket clients.')

      setMode((m) => (m === 'offline' ? 'offline' : 'polling'))
      startPolling()

      const delay = backoffRef.current
      backoffRef.current = Math.min(delay * 2, WS_BACKOFF_MAX_MS)
      setNextRetryAt(new Date(Date.now() + delay))
      retryTimerRef.current = setTimeout(connect, delay)
    }
  }, [accept, armSilenceTimer, startPolling, stopPolling])

  const reconnectNow = useCallback(() => {
    backoffRef.current = WS_BACKOFF_START_MS
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
