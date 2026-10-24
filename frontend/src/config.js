// Coordinator connection settings.
//
// By default everything is same-origin: CloudFront serves the dashboard and
// forwards /api/* to the coordinator, so no CORS and no need to know the EC2 IP
// (the coordinator security group only accepts CloudFront traffic and the
// coordinator requires X-Origin-Verify, which CloudFront adds).

export const API_BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '')

function defaultWsUrl() {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/api/ws`
}

export const WS_URL = import.meta.env.VITE_WS_URL || defaultWsUrl()

// Fallback while the WebSocket is down (coordinator contract).
export const POLL_INTERVAL_MS = 2000
// Reconnect backoff: 1 s, 2 s, 4 s, ... up to 30 s.
export const WS_BACKOFF_START_MS = 1000
export const WS_BACKOFF_MAX_MS = 30000
// The server sends {"type":"ping"} every 25 s. If 60 s pass without any
// message, we treat the socket as dead and reconnect.
export const WS_SILENCE_TIMEOUT_MS = 60000
// Load series (GET /api/metrics).
export const METRICS_INTERVAL_MS = 10000
export const METRICS_WINDOW_MIN = 30
