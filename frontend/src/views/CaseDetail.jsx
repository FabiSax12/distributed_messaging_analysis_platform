// Case detail: barrier, sub-tasks grouped by file, cancellation and
// consolidated report.
//
// GET /api/cases/{id} returns the case and all its sub-tasks. It is requested
// again whenever the live state shows a change in that case (by_status or
// status), and every 5 s while the case has not finished, in case the change
// was only in progress (%), which the state summary does not include.

import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, toDevS3Url } from '../api/client.js'
import { useAuth } from '../auth/AuthProvider.jsx'
import { AnalysisDetails, CATEGORY, FindingsList, Timeline } from '../components/Findings.jsx'
import { Empty, ErrorBanner, Section, StackedProgress, StatusLegend, StatusPill } from '../components/ui.jsx'
import { useInterval } from '../hooks/useInterval.js'
import { useLiveState } from '../hooks/useLiveState.jsx'
import { ERROR_CODES, MEDIA_TYPES, OPERATIONS, PRIORITIES, SUBTASK_STATUS, TERMINAL_CASE } from '../lib/domain.js'
import { basename, durationBetween, formatDateTime, formatDuration, formatTime, shortId } from '../lib/format.js'

const DETAIL_REFRESH_MS = 5000
// Presigned URLs live PRESIGN_EXPIRES_S (15 min by default): a finished case
// stops polling, so its input/output links are re-signed at this slower pace.
const PRESIGN_REFRESH_MS = 5 * 60 * 1000

export default function CaseDetail({ caseId }) {
  const { data: live } = useLiveState()
  const [detail, setDetail] = useState(null)
  const [outputs, setOutputs] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    // Independent on purpose: /outputs is an extra on top of the case detail;
    // if only that call fails (DynamoDB throttling, presign hiccup, etc.) it
    // must not take down a page that was already working.
    const [caseResult, outputsResult] = await Promise.allSettled([
      api.getCase(caseId),
      api.caseOutputs(caseId),
    ])
    if (caseResult.status === 'fulfilled') {
      setDetail(caseResult.value)
      setError(null)
    } else {
      setError(caseResult.reason)
    }
    if (outputsResult.status === 'fulfilled') setOutputs(outputsResult.value)
  }, [caseId])

  // subtask_id -> list of presigned URLs for its output_keys.
  const outputsBySubtask = useMemo(() => {
    const map = new Map()
    for (const s of outputs?.subtasks ?? []) map.set(s.subtask_id, s.outputs)
    return map
  }, [outputs])

  // input_key -> presigned URL of the input file (shared by its sub-tasks).
  const inputsByKey = useMemo(() => {
    const map = new Map()
    for (const s of outputs?.subtasks ?? []) if (s.input) map.set(s.input.key, s.input)
    return map
  }, [outputs])

  useEffect(() => {
    setDetail(null)
    load()
  }, [load])

  // Case signature in the live state: if it changes, we reload the detail.
  const summary = live?.cases.find((c) => c.case.case_id === caseId)
  const signature = summary ? `${summary.case.status}|${JSON.stringify(summary.by_status)}` : null
  useEffect(() => {
    if (signature) load()
  }, [signature, load])

  const terminal = detail && TERMINAL_CASE.has(detail.case.status)
  useInterval(load, detail ? (terminal ? PRESIGN_REFRESH_MS : DETAIL_REFRESH_MS) : null)

  if (error && !detail) {
    return (
      <div className="stack-lg">
        <a href="#/cases" className="back">← Case board</a>
        <ErrorBanner error={error.status === 404 ? 'That case does not exist.' : error} onRetry={load} />
      </div>
    )
  }
  if (!detail) return <Empty title="Loading case…" />

  return (
    <div className="stack-lg">
      <a href="#/cases" className="back">← Case board</a>
      <CaseHeader c={detail.case} onChanged={load} />
      <Barrier c={detail.case} subtasks={detail.subtasks} />
      {terminal && <Report caseId={caseId} status={detail.case.status} inputsByKey={inputsByKey} />}
      <SubTasks subtasks={detail.subtasks} outputsBySubtask={outputsBySubtask} inputsByKey={inputsByKey} />
    </div>
  )
}

function CaseHeader({ c, onChanged }) {
  const auth = useAuth()
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const terminal = TERMINAL_CASE.has(c.status)

  const cancel = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.cancelCase(c.case_id)
      setConfirming(false)
      onChanged()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <header className="case-header">
      <div className="case-title">
        <h1>{c.label || `Case ${shortId(c.case_id)}`}</h1>
        <div className="case-tags">
          <StatusPill status={c.status} />
          <span className={`pill tone-${PRIORITIES[c.priority]?.tone ?? 'neutral'}`}>{PRIORITIES[c.priority]?.label ?? c.priority} priority</span>
          <span className="pill tone-neutral">{c.source === 'auto' ? 'Auto-generated' : 'Manual'}</span>
          {c.cancel_requested && c.status !== 'cancelled' && <span className="pill tone-warn">Cancellation requested</span>}
        </div>
        <p className="mono muted small">{c.case_id}</p>
      </div>

      <dl className="facts">
        <div><dt>Created</dt><dd>{formatDateTime(c.created_at)}</dd></div>
        <div><dt>Started</dt><dd>{formatDateTime(c.started_at)}</dd></div>
        <div><dt>Finished</dt><dd>{formatDateTime(c.finished_at)}</dd></div>
        <div><dt>Duration</dt><dd className="num">{formatDuration(durationBetween(c.started_at || c.created_at, c.finished_at))}</dd></div>
      </dl>

      {!terminal && !c.cancel_requested && !auth.can('cancel') && (
        <p className="muted small case-actions">Cancelling a case requires the supervisor or administrator role.</p>
      )}
      {!terminal && !c.cancel_requested && auth.can('cancel') && (
        <div className="case-actions">
          {confirming ? (
            <div className="confirm">
              <span>Pending sub-tasks are discarded; running analysis processes are stopped and recorded as cancelled.</span>
              <button type="button" className="btn btn-danger" onClick={cancel} disabled={busy}>
                {busy ? 'Cancelling…' : 'Yes, cancel case'}
              </button>
              <button type="button" className="btn" onClick={() => setConfirming(false)} disabled={busy}>
                No
              </button>
            </div>
          ) : (
            <button type="button" className="btn" onClick={() => setConfirming(true)}>
              Cancel case
            </button>
          )}
          <ErrorBanner error={error} />
        </div>
      )}
    </header>
  )
}

// The barrier: pending_count starts at N and each terminal sub-task decrements it by 1.
function Barrier({ c, subtasks }) {
  const byStatus = useMemo(() => {
    const out = {}
    for (const s of subtasks) out[s.status] = (out[s.status] || 0) + 1
    return out
  }, [subtasks])
  const closed = c.total - c.pending_count

  return (
    <Section title="Barrier / join" aside={<span className="muted">The case closes when pending_count reaches 0</span>}>
      <div className="barrier">
        <div className="barrier-counter">
          <span className="kpi-label">pending_count</span>
          <span className="barrier-num num">{c.pending_count}</span>
          <span className="muted small">of {c.total} sub-tasks · {c.file_count} files</span>
        </div>
        <div className="barrier-body">
          <StackedProgress byStatus={byStatus} total={c.total} />
          <div className="barrier-stats">
            <span><b className="num">{closed}</b> resolved</span>
            <span><b className="num">{byStatus.completed || 0}</b> completed</span>
            <span><b className="num">{c.failed_count}</b> failed</span>
            <span><b className="num">{c.cancelled_count}</b> cancelled</span>
            <span><b className="num">{(byStatus.running || 0) + (byStatus.assigned || 0)}</b> in progress</span>
            <span><b className="num">{byStatus.retrying || 0}</b> retrying</span>
          </div>
          <StatusLegend />
        </div>
      </div>
    </Section>
  )
}

function SubTasks({ subtasks, outputsBySubtask, inputsByKey }) {
  const [filter, setFilter] = useState('all')

  const groups = useMemo(() => {
    const map = new Map()
    for (const s of subtasks) {
      if (filter !== 'all' && s.status !== filter) continue
      if (!map.has(s.input_key)) map.set(s.input_key, [])
      map.get(s.input_key).push(s)
    }
    return [...map.entries()]
  }, [subtasks, filter])

  const counts = useMemo(() => {
    const out = {}
    for (const s of subtasks) out[s.status] = (out[s.status] || 0) + 1
    return out
  }, [subtasks])

  return (
    <Section title="Sub-tasks" aside={<span className="muted">{subtasks.length} total, grouped by file</span>}>
      <div className="chips" role="group" aria-label="Filter sub-tasks">
        <button type="button" className={`chip ${filter === 'all' ? 'on' : ''}`} onClick={() => setFilter('all')}>
          All <span className="num">{subtasks.length}</span>
        </button>
        {Object.entries(SUBTASK_STATUS).map(([key, info]) =>
          counts[key] ? (
            <button key={key} type="button" className={`chip ${filter === key ? 'on' : ''}`} onClick={() => setFilter(key)}>
              {info.label} <span className="num">{counts[key]}</span>
            </button>
          ) : null,
        )}
      </div>

      <div className="table-wrap">
        <table className="table subtasks">
          <thead>
            <tr>
              <th>Operation</th>
              <th>Status</th>
              <th>Worker</th>
              <th>Queue</th>
              <th className="right">Attempt</th>
              <th>Progress</th>
              <th>Start</th>
              <th className="right">Duration</th>
            </tr>
          </thead>
          {groups.map(([inputKey, list]) => (
            <tbody key={inputKey}>
              <tr className="file-row">
                <td colSpan={8}>
                  <span className="mono">{basename(inputKey)}</span>
                  <span className="muted small"> · {MEDIA_TYPES[list[0].media_type] ?? 'Unsupported type'} · {inputKey}</span>
                  <InputFile file={inputsByKey.get(inputKey)} />
                </td>
              </tr>
              {list.map((s) => (
                <SubTaskRow key={s.subtask_id} s={s} outputs={outputsBySubtask.get(s.subtask_id) ?? []} />
              ))}
            </tbody>
          ))}
        </table>
      </div>
    </Section>
  )
}

// Extension -> inline preview type: what shared/routing.py accepts and a
// browser can plausibly decode (.avi has no browser codec support). Any other
// extension (metadata JSON, chat exports, etc.) only gets the download link.
const PREVIEW_KIND = {
  mp4: 'video', mkv: 'video', webm: 'video', mov: 'video',
  mp3: 'audio', wav: 'audio', ogg: 'audio', opus: 'audio', m4a: 'audio', aac: 'audio', flac: 'audio',
  jpg: 'image', jpeg: 'image', png: 'image', webp: 'image', gif: 'image', bmp: 'image',
}

const PREVIEW_LABEL = { image: 'View', audio: 'Listen', video: 'Play' }

function previewKind(key) {
  return PREVIEW_KIND[key.split('.').pop()?.toLowerCase()] ?? null
}

// Download link + inline preview of one presigned file ({key, url}). The
// media element only mounts while the <details> is open, so nothing is
// fetched up front, and it keeps the URL it opened with: polling re-signs
// every URL, and a new src would restart playback.
function MediaPreview({ file, label = basename(file.key) }) {
  const [src, setSrc] = useState(null)
  const url = toDevS3Url(file.url)
  const kind = previewKind(file.key)

  return (
    <span className="media-file">
      <a href={url} download>{label}</a>
      {kind && (
        <details className="media-preview" onToggle={(e) => setSrc(e.currentTarget.open ? url : null)}>
          <summary>{PREVIEW_LABEL[kind]}</summary>
          {src && kind === 'video' && <video controls preload="none" src={src} />}
          {src && kind === 'audio' && <audio controls preload="none" src={src} />}
          {src && kind === 'image' && <img src={src} alt={basename(file.key)} loading="lazy" />}
        </details>
      )}
    </span>
  )
}

// The original file a sub-task analyzed (GET /api/cases/{id}/outputs `input`).
function InputFile({ file }) {
  if (!file) return null // outputs not loaded yet or the call failed
  return (
    <span className="small input-file">
      <MediaPreview file={file} label="Original" />
    </span>
  )
}

// Downloads + inline previews of a sub-task's output_keys. The URLs are
// presigned (private bucket) and come from GET /api/cases/{id}/outputs.
function OutputFiles({ keys, outputs }) {
  const byKey = useMemo(() => new Map(outputs.map((o) => [o.key, o])), [outputs])

  return (
    <span className="muted small outputs">
      Output:{' '}
      {keys.map((k) => {
        const output = byKey.get(k)
        if (!output) return <code key={k}>{basename(k)}</code> // no presigned URL yet
        return <MediaPreview key={k} file={output} />
      })}
    </span>
  )
}

function SubTaskRow({ s, outputs }) {
  const running = s.status === 'running'
  const analysis = s.output_meta?.analysis
  return (
    <>
      <tr>
        <td>
          {OPERATIONS[s.operation] ?? '—'}
          <div className="mono muted small" title={s.subtask_id}>{shortId(s.subtask_id)}</div>
        </td>
        <td><StatusPill status={s.status} kind="subtask" /></td>
        <td className="mono small">{s.worker_id ?? '—'}</td>
        <td className="mono small">{s.queue ?? '—'}</td>
        <td className="right num">{s.attempt || '—'}</td>
        <td>
          {s.progress != null ? (
            <div className="mini-progress" title={`${s.progress} %`}>
              <span style={{ width: `${s.progress}%` }} className={running ? 'live' : ''} />
              <small className="num">{s.progress}%</small>
            </div>
          ) : (
            <span className="muted">—</span>
          )}
        </td>
        <td className="small num">{formatTime(s.started_at)}</td>
        <td className="right small num">{s.started_at ? formatDuration(durationBetween(s.started_at, s.finished_at)) : '—'}</td>
      </tr>
      {(s.error || s.output_keys.length > 0 || analysis) && (
        <tr className="sub-extra">
          <td colSpan={8}>
            {analysis && (
              <details className="sub-analysis">
                <summary className="small">
                  Analysis: {analysis.findings?.length ?? 0} finding(s)
                  {analysis.findings?.length > 0 && ` · ${[...new Set(analysis.findings.map((f) => CATEGORY[f.category] ?? f.category))].join(', ')}`}
                </summary>
                <AnalysisDetails analysis={analysis} />
              </details>
            )}
            {s.error && (
              <span className="err">
                <b>{ERROR_CODES[s.error.code] ?? s.error.code}:</b> {s.error.message}
              </span>
            )}
            {s.output_keys.length > 0 && <OutputFiles keys={s.output_keys} outputs={outputs} />}
          </td>
        </tr>
      )}
    </>
  )
}

// Consolidated report (results/{case_id}/report.json) via presigned GET URL.
function Report({ caseId, status, inputsByKey }) {
  const [state, setState] = useState({ loading: false })

  const open = async () => {
    setState({ loading: true })
    try {
      // Two steps: the API returns a presigned URL envelope; the report itself
      // is read from S3 WITHOUT the API bearer token.
      const { url } = await api.reportUrl(caseId)
      try {
        const res = await fetch(toDevS3Url(url), { credentials: 'omit' })
        if (!res.ok) throw new Error(`S3 responded ${res.status}`)
        setState({ url, report: await res.json() })
      } catch (err) {
        // The bucket only allows CORS from CloudFront (in dev it goes through the Vite proxy).
        setState({ url, fetchError: err.message })
      }
    } catch (err) {
      setState({ error: err })
    }
  }

  const r = state.report
  return (
    <Section
      title="Consolidated report"
      aside={
        <span className="row-gap">
          {state.url && (
            <a className="btn btn-small" href={state.url} target="_blank" rel="noreferrer">
              Download report.json
            </a>
          )}
          <button type="button" className="btn btn-primary btn-small" onClick={open} disabled={state.loading}>
            {state.loading ? 'Loading…' : r ? 'Refresh' : 'View report'}
          </button>
        </span>
      }
    >
      {!r && !state.error && !state.fetchError && (
        <p className="muted">
          The case ended as <StatusPill status={status} />. The coordinator wrote the report to S3 when the barrier closed.
        </p>
      )}
      <ErrorBanner error={state.error} />
      {state.fetchError && (
        <p className="muted small">
          Could not read the report from the browser ({state.fetchError}). Use the download button.
        </p>
      )}
      {r && (
        <div className="report">
          <p className="report-summary">{r.summary}</p>
          <dl className="facts">
            <div><dt>Files</dt><dd className="num">{r.file_count}</dd></div>
            <div><dt>Sub-tasks</dt><dd className="num">{r.subtask_count}</dd></div>
            <div><dt>Duration</dt><dd className="num">{formatDuration(r.duration_s)}</dd></div>
            <div><dt>Workers</dt><dd className="num">{r.workers_used.length}</dd></div>
          </dl>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Operation</th>
                  <th className="right">Total</th>
                  <th className="right">Completed</th>
                  <th className="right">Failed</th>
                  <th className="right">Cancelled</th>
                </tr>
              </thead>
              <tbody>
                {r.groups.map((g, i) => (
                  <tr key={i}>
                    <td>{MEDIA_TYPES[g.media_type] ?? 'Unsupported'}</td>
                    <td>{OPERATIONS[g.operation] ?? '—'}</td>
                    <td className="right num">{g.total}</td>
                    <td className="right num">{g.completed}</td>
                    <td className="right num">{g.failed}</td>
                    <td className="right num">{g.cancelled}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small muted">
            Responsible workers: {r.workers_used.map((w) => <code key={w}>{w}</code>)}
          </p>

          <h3 className="report-h">Findings by category</h3>
          {Object.keys(r.categories || {}).length ? (
            <div className="category-tiles">
              {Object.entries(r.categories).map(([c, n]) => (
                <div key={c} className="category-tile">
                  <span className="num">{n}</span>
                  <span className="small">{CATEGORY[c] ?? c}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted">No findings in this case.</p>
          )}
          <p className="muted small">
            Findings are review cues from keyword rules and object detection; scores are not calibrated probabilities.
            Every finding links to its source file, hash and location.
          </p>

          <h3 className="report-h">Findings</h3>
          <FindingsList findings={r.findings || []} />

          <h3 className="report-h">Timeline</h3>
          <Timeline events={r.timeline || []} />

          <h3 className="report-h">Per-file analysis</h3>
          <ul className="file-analyses">
            {(r.subtasks || []).map((st) => (
              <li key={st.subtask_id}>
                <details>
                  <summary>
                    <span className="mono">{basename(st.input_key)}</span>{' '}
                    <StatusPill status={st.status} kind="subtask" />{' '}
                    <span className="muted small">{st.analysis ? `${st.analysis.findings?.length ?? 0} finding(s)` : st.error ? ERROR_CODES[st.error.code] ?? st.error.code : 'no analysis'}</span>
                  </summary>
                  {inputsByKey.has(st.input_key) && (
                    <div className="small file-input"><MediaPreview file={inputsByKey.get(st.input_key)} label="Original file" /></div>
                  )}
                  {st.analysis ? <AnalysisDetails analysis={st.analysis} /> : st.error && <p className="err small">{st.error.message}</p>}
                </details>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Section>
  )
}
