// Case board: open cases and the latest finished ones carried by the live state,
// as cards filtered by status.

import { useMemo, useState } from 'react'
import { Empty, Section, StackedProgress, StatusLegend, StatusPill } from '../components/ui.jsx'
import { useLiveState } from '../hooks/useLiveState.jsx'
import { CASE_STATUS, PRIORITIES } from '../lib/domain.js'
import { durationBetween, formatDateTime, formatDuration, shortId } from '../lib/format.js'

export default function CasesList() {
  const { data } = useLiveState()
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')

  const cases = useMemo(() => {
    const list = (data?.cases ?? []).slice().sort((a, b) => b.case.case_id.localeCompare(a.case.case_id))
    const q = query.trim().toLowerCase()
    return list.filter(
      ({ case: c }) =>
        (filter === 'all' || c.status === filter) &&
        (!q || c.case_id.toLowerCase().includes(q) || (c.label || '').toLowerCase().includes(q)),
    )
  }, [data, filter, query])

  const counts = useMemo(() => {
    const out = {}
    for (const { case: c } of data?.cases ?? []) out[c.status] = (out[c.status] || 0) + 1
    return out
  }, [data])

  return (
    <Section
      title="Case board"
      aside={
        <a className="btn btn-primary" href="#/new">
          Open a case
        </a>
      }
    >
      <div className="toolbar">
        <div className="board-tabs" role="tablist" aria-label="Filter by status">
          <button type="button" role="tab" aria-selected={filter === 'all'} className={`board-tab ${filter === 'all' ? 'on' : ''}`} onClick={() => setFilter('all')}>
            All <span className="num">{data?.cases.length ?? 0}</span>
          </button>
          {Object.entries(CASE_STATUS).map(([key, info]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={filter === key}
              className={`board-tab ${filter === key ? 'on' : ''}`}
              onClick={() => setFilter(key)}
              disabled={!counts[key]}
            >
              {info.label} <span className="num">{counts[key] || 0}</span>
            </button>
          ))}
        </div>
        <input
          id="case-search"
          className="input search"
          type="search"
          placeholder="Search by label or ID"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <StatusLegend />

      {cases.length === 0 ? (
        <Empty title={data ? 'No matching cases' : 'Loading cases…'}>
          The live state includes open cases and the 50 most recently finished.
        </Empty>
      ) : (
        <ul className="case-cards">
          {cases.map(({ case: c, by_status }) => (
            <li key={c.case_id}>
              <a href={`#/cases/${c.case_id}`} className={`case-card prio-${c.priority}`}>
                <div className="case-card-top">
                  <StatusPill status={c.status} />
                  <span className={`pill tone-${PRIORITIES[c.priority]?.tone ?? 'neutral'}`}>{PRIORITIES[c.priority]?.label ?? c.priority}</span>
                </div>
                <strong className="case-card-title">{c.label || `Case ${shortId(c.case_id)}`}</strong>
                <span className="mono muted small">{shortId(c.case_id)}</span>
                <StackedProgress byStatus={by_status} total={c.total} />
                <div className="case-card-facts small muted">
                  <span className="num">{c.total - c.pending_count}/{c.total} sub-tasks{c.failed_count ? ` · ${c.failed_count} failed` : ''}</span>
                  <span className="num">{c.file_count} file(s)</span>
                </div>
                <div className="case-card-facts small muted">
                  <span>{formatDateTime(c.created_at)}</span>
                  <span className="num">{formatDuration(durationBetween(c.started_at || c.created_at, c.finished_at))}</span>
                </div>
                {c.cancel_requested && c.status !== 'cancelled' && <span className="small tone-text-bad">Cancellation requested</span>}
                <span className="case-card-source small muted">{c.source === 'auto' ? 'Automatic' : 'Manual'}</span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}
