// Indicator of how data reaches the dashboard: WebSocket, fallback polling
// or offline. Lets the user reconnect manually without waiting for the backoff.

import { useEffect, useState } from 'react'
import { useLiveState } from '../hooks/useLiveState.jsx'
import { formatTime } from '../lib/format.js'

const LABELS = {
  connecting: { text: 'Connecting…', tone: 'neutral' },
  ws: { text: 'Live · WebSocket', tone: 'ok' },
  polling: { text: 'Fallback · polling 2 s', tone: 'warn' },
  offline: { text: 'Offline', tone: 'bad' },
}

export default function ConnectionBadge() {
  const { mode, receivedAt, nextRetryAt, lastError, reconnectNow } = useLiveState()
  const [, tick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(id)
  }, [])

  const info = LABELS[mode]
  const retryIn = nextRetryAt ? Math.max(0, Math.ceil((nextRetryAt - Date.now()) / 1000)) : null
  const detail = [
    receivedAt && `Last data ${formatTime(receivedAt.toISOString())}`,
    mode !== 'ws' && retryIn != null && `WebSocket retrying in ${retryIn} s`,
    lastError,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <div className="conn" title={detail}>
      <span className={`conn-dot tone-${info.tone}`} aria-hidden="true" />
      <span className="conn-text">
        <strong>{info.text}</strong>
        <small>{detail || 'Waiting for the first coordinator state'}</small>
      </span>
      {mode !== 'ws' && (
        <button type="button" className="btn btn-small" onClick={reconnectNow}>
          Reconnect
        </button>
      )}
    </div>
  )
}
