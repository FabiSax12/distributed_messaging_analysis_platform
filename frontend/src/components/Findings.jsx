// Findings, evidence, timeline and per-file analysis (shared/analysis.py).
//
// All user text (excerpts, transcripts, labels) is rendered as React text
// nodes, so it is escaped; nothing here uses raw HTML. Scores from the text
// rules are fixed review cues, not calibrated probabilities. Evidence
// timestamps are the ORIGINAL message times from the export; processing times
// are when the platform ran. An evidence item without a timestamp has an
// unknown original date and is shown as such.

import { useMemo, useState } from 'react'
import { basename, formatDateTime } from '../lib/format.js'

export const CATEGORY = {
  threat: 'Threat',
  violence: 'Violence',
  offensive_language: 'Offensive language',
  illicit_activity: 'Illicit activity',
  sensitive_image: 'Sensitive image',
  other: 'Other',
}
const SEVERITY_TONE = { low: 'neutral', medium: 'warn', high: 'bad', critical: 'crit' }
const SEVERITY_RANK = { critical: 0, high: 1, medium: 2, low: 3 }

function seconds(s) {
  if (s == null) return '?'
  const m = Math.floor(s / 60)
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, '0')}`
}

export function Severity({ value }) {
  return <span className={`pill sev sev-${SEVERITY_TONE[value] ?? 'neutral'}`}>{value}</span>
}

export function EvidenceView({ ev }) {
  return (
    <div className="evidence">
      <div className="evidence-loc">
        <span className="mono" title={ev.source_key}>{basename(ev.source_key)}</span>
        {ev.kind === 'message' && <span className="loc">message #{ev.message_index}</span>}
        {ev.kind === 'audio_segment' && <span className="loc">audio {seconds(ev.audio_start_s)} – {seconds(ev.audio_end_s)}</span>}
        {ev.kind === 'image' &&
          (ev.bbox ? (
            <span className="loc" title="Normalized box: x, y, width, height (0-1)">
              box x {ev.bbox.x.toFixed(2)} y {ev.bbox.y.toFixed(2)} w {ev.bbox.w.toFixed(2)} h {ev.bbox.h.toFixed(2)}
            </span>
          ) : (
            <span className="loc">whole image</span>
          ))}
        {ev.sender && <span className="loc">from {ev.sender}</span>}
        <span className="loc" title="Original time in the export (evidence time)">
          {ev.timestamp ? `sent ${formatDateTime(ev.timestamp)}` : 'original date unknown'}
        </span>
      </div>
      {ev.excerpt && <blockquote className="excerpt">{ev.excerpt}</blockquote>}
      {ev.sha256 && <div className="mono muted tiny" title="SHA-256 of the source file">sha256 {ev.sha256.slice(0, 16)}…</div>}
    </div>
  )
}

export function FindingCard({ f }) {
  return (
    <li className={`finding sev-border-${SEVERITY_TONE[f.severity] ?? 'neutral'}`}>
      <div className="finding-head">
        <span className="finding-cat">{CATEGORY[f.category] ?? f.category}</span>
        <Severity value={f.severity} />
        <span className="muted small" title="Rule/model score, not a calibrated probability">score {f.confidence.toFixed(2)}</span>
        <span className="small">{f.label}</span>
      </div>
      {f.evidence.map((ev, i) => <EvidenceView key={i} ev={ev} />)}
    </li>
  )
}

export function FindingsList({ findings, limit = 25 }) {
  const [category, setCategory] = useState('all')
  const [shown, setShown] = useState(limit)
  const sorted = useMemo(
    () => [...findings].sort((a, b) => (SEVERITY_RANK[a.severity] ?? 9) - (SEVERITY_RANK[b.severity] ?? 9)),
    [findings],
  )
  const counts = useMemo(() => {
    const out = {}
    for (const f of findings) out[f.category] = (out[f.category] || 0) + 1
    return out
  }, [findings])
  if (!findings.length) {
    return <p className="muted">No findings. An empty result is valid: the analyzers found no rule or detector match.</p>
  }
  const visible = sorted.filter((f) => category === 'all' || f.category === category)
  return (
    <div className="stack">
      <div className="chips" role="group" aria-label="Filter findings by category">
        <button type="button" className={`chip ${category === 'all' ? 'on' : ''}`} onClick={() => setCategory('all')}>
          All <span className="num">{findings.length}</span>
        </button>
        {Object.entries(counts).map(([c, n]) => (
          <button key={c} type="button" className={`chip ${category === c ? 'on' : ''}`} onClick={() => setCategory(c)}>
            {CATEGORY[c] ?? c} <span className="num">{n}</span>
          </button>
        ))}
      </div>
      <ul className="findings">
        {visible.slice(0, shown).map((f, i) => <FindingCard key={i} f={f} />)}
      </ul>
      {visible.length > shown && (
        <button type="button" className="btn btn-small" onClick={() => setShown((n) => n + limit)}>
          Show {Math.min(limit, visible.length - shown)} more of {visible.length - shown}
        </button>
      )}
    </div>
  )
}

export function Timeline({ events }) {
  const [kind, setKind] = useState('finding')
  const list = events.filter((e) => kind === 'all' || e.type === kind)
  const undated = list.filter((e) => !e.time).length
  return (
    <div className="stack">
      <div className="chips" role="group" aria-label="Timeline events">
        {[['finding', 'Evidence (original time)'], ['processing', 'Processing (platform time)'], ['all', 'Both']].map(([k, label]) => (
          <button key={k} type="button" className={`chip ${kind === k ? 'on' : ''}`} onClick={() => setKind(k)}>{label}</button>
        ))}
      </div>
      {!list.length && <p className="muted">No events.</p>}
      <ol className="timeline">
        {list.slice(0, 200).map((e, i) => (
          <li key={i} className={`tl tl-${e.type}`}>
            <span className="tl-time num">{e.time ? formatDateTime(e.time) : 'date unknown'}</span>
            {e.type === 'finding' ? (
              <span>
                <b>{CATEGORY[e.category] ?? e.category}</b> <Severity value={e.severity} />{' '}
                <span className="mono small">{basename(e.evidence?.source_key ?? '')}</span>
                {e.evidence?.message_index != null && <span className="muted small"> #{e.evidence.message_index}</span>}
                {e.evidence?.excerpt && <span className="tl-excerpt"> “{e.evidence.excerpt}”</span>}
              </span>
            ) : (
              <span className="muted">
                processed <span className="mono small">{basename(e.input_key ?? '')}</span> → {e.status}
              </span>
            )}
          </li>
        ))}
      </ol>
      {list.length > 200 && <p className="muted small">Showing the first 200 of {list.length} events.</p>}
      {undated > 0 && <p className="muted small">{undated} event(s) have no original timestamp (e.g. audio or images): their date is unknown, not the processing time.</p>}
    </div>
  )
}

// Per-file analysis: model, timings, keywords, sentiment, transcript, labels, findings.
export function AnalysisDetails({ analysis }) {
  if (!analysis) return null
  return (
    <div className="analysis">
      <div className="analysis-meta small">
        {analysis.model && <span>model <b>{analysis.model.name}</b>{analysis.model.version ? ` v${analysis.model.version}` : ''}</span>}
        {Object.entries(analysis.timings_s || {}).map(([k, v]) => <span key={k}>{k} {v.toFixed(2)} s</span>)}
        {analysis.sentiment && <span>sentiment {analysis.sentiment.label} ({analysis.sentiment.score.toFixed(2)})</span>}
      </div>
      {analysis.keywords?.length > 0 && (
        <p className="small"><span className="muted">Keywords:</span> {analysis.keywords.join(', ')}</p>
      )}
      {analysis.image_labels?.length > 0 && (
        <p className="small">
          <span className="muted">Detected objects:</span>{' '}
          {analysis.image_labels.map((l) => `${l.label} ${l.confidence.toFixed(2)}`).join(', ')}
        </p>
      )}
      {analysis.transcript && (
        <details>
          <summary className="small">Transcript ({analysis.transcript.language || 'unknown language'}, {analysis.transcript.segments?.length ?? 0} segments)</summary>
          <ol className="segments">
            {(analysis.transcript.segments || []).map((s, i) => (
              <li key={i}><span className="num muted">{seconds(s.start_s)}–{seconds(s.end_s)}</span> {s.text}</li>
            ))}
          </ol>
        </details>
      )}
      {analysis.findings?.length > 0 ? (
        <details>
          <summary className="small">{analysis.findings.length} finding(s)</summary>
          <FindingsList findings={analysis.findings} limit={10} />
        </details>
      ) : (
        <p className="muted small">No findings in this file.</p>
      )}
    </div>
  )
}
