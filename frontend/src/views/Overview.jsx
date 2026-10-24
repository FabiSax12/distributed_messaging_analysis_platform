// Overview: summary, alerts, worker nodes, queues and load behavior.

import { useMemo, useState } from 'react'
import { api } from '../api/client.js'
import LineChart from '../components/LineChart.jsx'
import { Empty, Meter, Section, StackedProgress, StatusPill } from '../components/ui.jsx'
import { METRICS_INTERVAL_MS, METRICS_WINDOW_MIN } from '../config.js'
import { useInterval } from '../hooks/useInterval.js'
import { useLiveState } from '../hooks/useLiveState.jsx'
import { POLL_ORDER, POOLS, TERMINAL_CASE } from '../lib/domain.js'
import { secondsAgo, shortId } from '../lib/format.js'

const POOL_COLORS = { video: 'var(--c-video)', audio: 'var(--c-audio)', metadatos: 'var(--c-meta)' }

export default function Overview() {
  const { data } = useLiveState()

  if (!data) {
    return <Empty title="Waiting for the first coordinator state">Check the connection indicator at the top right.</Empty>
  }

  return (
    <div className="stack-lg">
      <Kpis data={data} />
      <Alerts alerts={data.alerts} />
      <Workers workers={data.workers} />
      <div className="grid-2">
        <Queues queues={data.queues} />
        <ActiveCases cases={data.cases} />
      </div>
      <LoadHistory />
    </div>
  )
}

function Kpis({ data }) {
  const k = useMemo(() => {
    const open = data.cases.filter((c) => !TERMINAL_CASE.has(c.case.status))
    const sum = (key) => data.cases.reduce((a, c) => a + (c.by_status[key] || 0), 0)
    const work = data.queues.filter((q) => q.queue !== 'resultados' && q.queue !== 'dlq')
    const dlq = data.queues.find((q) => q.queue === 'dlq')
    return {
      open: open.length,
      active: sum('running') + sum('assigned') + sum('retrying'),
      waiting: sum('pending'),
      alive: data.workers.filter((w) => w.alive).length,
      workers: data.workers.length,
      queued: work.reduce((a, q) => a + q.visible, 0),
      inFlight: work.reduce((a, q) => a + q.in_flight, 0),
      dlq: dlq ? dlq.visible + dlq.in_flight : 0,
    }
  }, [data])

  return (
    <div className="kpis">
      <Kpi label="Open cases" value={k.open} />
      <Kpi label="Sub-tasks in progress" value={k.active} hint={`${k.waiting} waiting`} />
      <Kpi
        label="Live workers"
        value={`${k.alive}/${k.workers}`}
        tone={k.workers && k.alive < k.workers ? 'warn' : undefined}
      />
      <Kpi label="Messages in queues" value={k.queued} hint={`${k.inFlight} taken by workers`} />
      <Kpi label="DLQ" value={k.dlq} tone={k.dlq ? 'bad' : undefined} hint="retries exhausted" />
    </div>
  )
}

function Kpi({ label, value, hint, tone }) {
  return (
    <div className={`kpi ${tone ? `tone-${tone}` : ''}`}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value num">{value}</span>
      {hint && <span className="kpi-hint">{hint}</span>}
    </div>
  )
}

function Alerts({ alerts = [] }) {
  if (!alerts.length) return null
  return (
    <div className="alerts">
      {alerts.map((a, i) => (
        <div key={`${a.pool}-${a.message}-${i}`} className={`banner tone-${a.level === 'critical' ? 'bad' : a.level === 'warning' ? 'warn' : 'info'}`}>
          <strong>{a.pool !== '-' ? `Pool ${a.pool}` : 'System'}</strong>
          <span>{a.message}</span>
        </div>
      ))}
    </div>
  )
}

function Workers({ workers }) {
  const byPool = Object.keys(POOLS).map((pool) => ({
    pool,
    list: workers.filter((w) => w.pool === pool).sort((a, b) => a.worker_id.localeCompare(b.worker_id)),
  }))

  return (
    <Section title="Worker nodes" aside={<span className="muted">Heartbeat every 5 s · marked down after 15 s without reporting</span>}>
      <div className="pools">
        {byPool.map(({ pool, list }) => (
          <div key={pool} className="pool">
            <header className="pool-head">
              <i className="dot" style={{ background: POOL_COLORS[pool] }} />
              <h3>Pool {POOLS[pool].label}</h3>
              <span className="muted mono">{POOLS[pool].instance}</span>
            </header>
            {list.length === 0 && <p className="muted small">No worker has registered a heartbeat.</p>}
            {list.map((w) => (
              <WorkerCard key={w.worker_id} w={w} />
            ))}
          </div>
        ))}
      </div>
    </Section>
  )
}

function WorkerCard({ w }) {
  const ago = secondsAgo(w.last_seen)
  return (
    <article className={`worker ${w.alive ? '' : 'dead'}`}>
      <div className="worker-head">
        <span className="mono worker-id" title={w.worker_id}>
          {w.worker_id}
        </span>
        <span className={`pill tone-${w.alive ? 'ok' : 'bad'}`}>{w.alive ? 'Alive' : 'Down'}</span>
      </div>
      <div className="worker-meta muted small">
        <span>{w.hostname}</span>
        <span>{w.instance_type}</span>
        <span>{ago == null ? '—' : Math.round(ago)} s ago</span>
      </div>
      <Meter label="CPU" value={w.cpu_percent} />
      <Meter label="Memory" value={w.mem_percent} />
      <div className="worker-slots">
        <span className="small">Active sub-tasks</span>
        <span className="slots" aria-label={`${w.active_subtasks.length} of ${w.concurrency}`}>
          {Array.from({ length: Math.max(w.concurrency, w.active_subtasks.length) }, (_, i) => (
            <i key={i} className={i < w.active_subtasks.length ? 'on' : ''} />
          ))}
        </span>
        <span className="num small">
          {w.active_subtasks.length}/{w.concurrency}
        </span>
      </div>
    </article>
  )
}

function Queues({ queues }) {
  const get = (name) => queues.find((q) => q.queue === name) ?? { visible: 0, in_flight: 0 }
  const max = Math.max(1, ...queues.map((q) => q.visible + q.in_flight))

  const row = (name, label) => {
    const q = get(name)
    return (
      <div key={name} className="queue-row">
        <span className="queue-name mono">{label ?? name}</span>
        <div className="queue-bar" title={`${q.visible} visible · ${q.in_flight} in flight`}>
          <span className="qb-visible" style={{ width: `${(q.visible / max) * 100}%` }} />
          <span className="qb-flight" style={{ width: `${(q.in_flight / max) * 100}%` }} />
        </div>
        <span className="num queue-count">
          {q.visible}
          <small> / {q.in_flight}</small>
        </span>
      </div>
    )
  }

  return (
    <Section
      title="SQS queues"
      aside={
        <span className="legend">
          <span className="legend-item"><i className="dot qb-visible" /> visible</span>
          <span className="legend-item"><i className="dot qb-flight" /> in flight</span>
        </span>
      }
    >
      <div className="queues">
        {Object.keys(POOLS).map((pool) => (
          <div key={pool} className="queue-group">
            <div className="queue-group-head">
              <i className="dot" style={{ background: POOL_COLORS[pool] }} /> {POOLS[pool].label}
              <span className="muted small poll-order">consumes: {POLL_ORDER[pool].join(' → ')}</span>
            </div>
            {row(`${pool}-alta`, 'high')}
            {row(`${pool}-normal`, 'normal')}
          </div>
        ))}
        <div className="queue-group">
          <div className="queue-group-head">Coordinator</div>
          {row('resultados')}
          {row('dlq', 'DLQ')}
        </div>
      </div>
    </Section>
  )
}

function ActiveCases({ cases }) {
  const open = cases
    .filter((c) => !TERMINAL_CASE.has(c.case.status))
    .sort((a, b) => b.case.case_id.localeCompare(a.case.case_id))

  return (
    <Section title="Cases in progress" aside={<a href="#/cases">View all</a>}>
      {open.length === 0 ? (
        <Empty title="No cases in progress">
          <a href="#/new">Create a case</a>
        </Empty>
      ) : (
        <ul className="case-mini">
          {open.slice(0, 12).map(({ case: c, by_status }) => {
            const done = c.total - c.pending_count
            return (
              <li key={c.case_id}>
                <a href={`#/cases/${c.case_id}`} className="case-mini-row">
                  <span className="case-mini-title">
                    <strong>{c.label || `Case ${shortId(c.case_id)}`}</strong>
                    {c.priority === 'alta' && <span className="pill tone-accent">High</span>}
                  </span>
                  <StatusPill status={c.status} />
                  <StackedProgress byStatus={by_status} total={c.total} />
                  <span className="num small muted">
                    {done}/{c.total} sub-tasks
                  </span>
                </a>
              </li>
            )
          })}
        </ul>
      )}
    </Section>
  )
}

// Load series for the last 30 min (coordinator ring buffer, 1 sample / 5 s).
function LoadHistory() {
  const [samples, setSamples] = useState([])
  const [error, setError] = useState(null)

  useInterval(
    async () => {
      try {
        const res = await api.metrics(METRICS_WINDOW_MIN)
        setSamples(res.samples)
        setError(null)
      } catch (err) {
        setError(err.message)
      }
    },
    METRICS_INTERVAL_MS,
    { immediate: true },
  )

  const { times, queueSeries, cpuSeries } = useMemo(() => {
    const pools = Object.keys(POOLS)
    const times = samples.map((s) => s.ts)
    const queueSeries = pools.map((pool) => ({
      key: pool,
      label: POOLS[pool].label,
      color: POOL_COLORS[pool],
      values: samples.map((s) => (s.queue_visible[`${pool}-alta`] || 0) + (s.queue_visible[`${pool}-normal`] || 0)),
    }))
    const cpuSeries = pools.map((pool) => ({
      key: pool,
      label: POOLS[pool].label,
      color: POOL_COLORS[pool],
      values: samples.map((s) => {
        const ws = Object.values(s.workers).filter((w) => w.pool === pool && w.alive)
        return ws.length ? ws.reduce((a, w) => a + w.cpu_percent, 0) / ws.length : 0
      }),
    }))
    return { times, queueSeries, cpuSeries }
  }, [samples])

  return (
    <Section
      title="Load behavior"
      aside={<span className="muted">Last {METRICS_WINDOW_MIN} min · GET /api/metrics</span>}
    >
      {error && <p className="muted small">Could not read the load series: {error}</p>}
      <div className="grid-2">
        <div>
          <h3 className="chart-title">Messages waiting per pool</h3>
          <LineChart times={times} series={queueSeries} />
        </div>
        <div>
          <h3 className="chart-title">Average CPU per pool</h3>
          <LineChart times={times} series={cpuSeries} max={100} unit="%" />
        </div>
      </div>
    </Section>
  )
}
