// Operations view: platform status, pools (nodes + their queue), active cases,
// coordinator queues and recent activity.

import { useMemo, useState } from 'react'
import { api } from '../api/client.js'
import LineChart from '../components/LineChart.jsx'
import { Empty, Meter, Section, StackedProgress, StatusPill } from '../components/ui.jsx'
import { METRICS_INTERVAL_MS, METRICS_WINDOW_MIN } from '../config.js'
import { useInterval } from '../hooks/useInterval.js'
import { useLiveState } from '../hooks/useLiveState.jsx'
import { DEAD_LETTER_QUEUE, POOLS, PRIORITIES, RESULTS_QUEUE, TERMINAL_CASE, workQueue } from '../lib/domain.js'
import { secondsAgo, shortId } from '../lib/format.js'

const POOL_COLORS = Object.fromEntries(Object.entries(POOLS).map(([pool, p]) => [pool, p.color]))

export default function Overview() {
  const { data } = useLiveState()

  if (!data) {
    return <Empty title="Waiting for the first coordinator state">Check the connection indicator in the side menu.</Empty>
  }

  return (
    <div className="stack-lg">
      <StatusHero data={data} />
      <Alerts alerts={data.alerts} />
      <div className="ops-grid">
        <PoolBoard workers={data.workers} queues={data.queues} />
        <div className="stack-lg">
          <ActiveCases cases={data.cases} />
          <CoordinatorQueues queues={data.queues} />
        </div>
      </div>
      <LoadHistory />
    </div>
  )
}

function StatusHero({ data }) {
  const k = useMemo(() => {
    const open = data.cases.filter((c) => !TERMINAL_CASE.has(c.case.status))
    const sum = (key) => data.cases.reduce((a, c) => a + (c.by_status[key] || 0), 0)
    const work = data.queues.filter((q) => q.queue !== RESULTS_QUEUE && q.queue !== DEAD_LETTER_QUEUE)
    const dlq = data.queues.find((q) => q.queue === DEAD_LETTER_QUEUE)
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

  const degraded = k.workers && k.alive < k.workers
  const tone = k.dlq || !k.alive ? 'bad' : degraded || data.alerts?.length ? 'warn' : 'ok'
  const headline = {
    ok: 'All analysis pools are online',
    warn: degraded ? `${k.workers - k.alive} worker node(s) not reporting` : 'Running with warnings',
    bad: k.dlq ? `${k.dlq} sub-task(s) exhausted their retries` : 'No worker is online',
  }[tone]

  return (
    <section className={`status-hero tone-${tone}`}>
      <div className="status-hero-text">
        <span className="eyebrow">Operations</span>
        <h1>{headline}</h1>
        <p className="muted">
          {k.open ? `${k.open} case(s) under analysis, ${k.active} sub-task(s) running and ${k.waiting} waiting.` : 'No case is under analysis right now.'}
        </p>
      </div>
      <div className="stat-strip">
        <Kpi label="Cases open" value={k.open} />
        <Kpi label="Running" value={k.active} hint={`${k.waiting} waiting`} />
        <Kpi label="Nodes online" value={`${k.alive}/${k.workers}`} tone={degraded ? 'warn' : undefined} />
        <Kpi label="Queued messages" value={k.queued} hint={`${k.inFlight} in flight`} />
        <Kpi label="Dead letters" value={k.dlq} tone={k.dlq ? 'bad' : undefined} />
      </div>
    </section>
  )
}

function Kpi({ label, value, hint, tone }) {
  return (
    <div className={`stat ${tone ? `tone-${tone}` : ''}`}>
      <span className="stat-value num">{value}</span>
      <span className="stat-label">{label}</span>
      {hint && <span className="stat-hint">{hint}</span>}
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

function PoolBoard({ workers, queues }) {
  const byPool = Object.keys(POOLS).map((pool) => {
    const list = workers.filter((w) => w.pool === pool).sort((a, b) => a.worker_id.localeCompare(b.worker_id))
    const instanceTypes = [...new Set(list.filter((w) => w.alive).map((w) => w.instance_type).filter(Boolean))].join(', ')
    const q = queues.find((x) => x.queue === workQueue(pool)) ?? { visible: 0, in_flight: 0 }
    const alive = list.filter((w) => w.alive).length
    return { pool, list, instanceTypes, q, alive }
  })

  return (
    <Section title="Analysis pools" aside={<span className="muted small">Heartbeat every 5 s · down after 60 s silent</span>}>
      <div className="pool-board">
        {byPool.map(({ pool, list, instanceTypes, q, alive }) => (
          <div key={pool} className="pool-col" style={{ '--pool': POOL_COLORS[pool] }}>
            <header className="pool-col-head">
              <span className={`health ${alive ? (alive < list.length ? 'warn' : 'ok') : 'bad'}`} aria-hidden="true" />
              <div>
                <h3>{POOLS[pool].label}</h3>
                <span className="muted small mono">{instanceTypes || 'no live node'}</span>
              </div>
            </header>
            <div className="pool-queue">
              <span className="small muted mono">{workQueue(pool)}</span>
              <span className="pool-queue-num num">{q.visible}</span>
              <span className="small muted">waiting · {q.in_flight} in flight</span>
            </div>
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

function CoordinatorQueues({ queues }) {
  const get = (name) => queues.find((q) => q.queue === name) ?? { visible: 0, in_flight: 0 }
  return (
    <Section title="Coordinator queues">
      <ul className="coord-queues">
        {[[RESULTS_QUEUE, 'Results from workers'], [DEAD_LETTER_QUEUE, 'Dead letters (retries exhausted)']].map(([name, label]) => {
          const q = get(name)
          return (
            <li key={name}>
              <div>
                <span className="mono small">{name}</span>
                <span className="muted small">{label}</span>
              </div>
              <span className={`num coord-count ${name === DEAD_LETTER_QUEUE && q.visible + q.in_flight ? 'tone-text-bad' : ''}`}>
                {q.visible}
                <small> / {q.in_flight}</small>
              </span>
            </li>
          )
        })}
      </ul>
    </Section>
  )
}

function ActiveCases({ cases }) {
  const open = cases
    .filter((c) => !TERMINAL_CASE.has(c.case.status))
    .sort((a, b) => b.case.case_id.localeCompare(a.case.case_id))

  return (
    <Section title="Active cases" aside={<a href="#/cases">Case board</a>}>
      {open.length === 0 ? (
        <Empty title="No cases in progress">
          <a href="#/new">Open a case</a>
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
                    {c.priority !== 'medium' && <span className={`pill tone-${PRIORITIES[c.priority]?.tone ?? 'neutral'}`}>{PRIORITIES[c.priority]?.label ?? c.priority}</span>}
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
      values: samples.map((s) => s.queue_visible[workQueue(pool)] || 0),
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
      title="Activity"
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
