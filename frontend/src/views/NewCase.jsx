// Case submission. Three ways:
//  1. From the dataset: pick files that are already in S3 (GET /api/dataset).
//  2. Upload files: POST /api/uploads returns presigned PUT URLs, the browser
//     uploads each file straight to S3 and then the case is created with those keys.
//  3. Auto-generation: group the dataset by folder or by a metadata field and
//     create one case per group, all in parallel.
// In all three, the per-file operation is optional: if none is chosen, the
// coordinator decides (DEFAULT_OPERATIONS in shared/routing.py).

import { useEffect, useMemo, useState } from 'react'
import { api, putToS3 } from '../api/client.js'
import { Empty, ErrorBanner, Section } from '../components/ui.jsx'
import { navigate } from '../hooks/useHashRoute.js'
import { ALLOWED_OPERATIONS, DEFAULT_OPERATIONS, MEDIA_TYPES, OPERATIONS, detectMediaType } from '../lib/domain.js'
import { basename, formatBytes, shortId } from '../lib/format.js'

const TABS = [
  { key: 'dataset', label: 'From the dataset' },
  { key: 'upload', label: 'Upload files' },
  { key: 'auto', label: 'Auto-generation' },
]

export default function NewCase() {
  const [tab, setTab] = useState('dataset')
  return (
    <div className="stack-lg">
      <header className="page-head">
        <h1>New case</h1>
        <p className="muted">
          A case groups one or more files, of the same type or mixed. The coordinator splits it into sub-tasks and
          queues them by type and priority.
        </p>
      </header>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            className={`tab ${tab === t.key ? 'on' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'dataset' && <FromDataset />}
      {tab === 'upload' && <FromUpload />}
      {tab === 'auto' && <AutoGenerate />}
    </div>
  )
}

// ---- Shared pieces -------------------------------------------------------- //

function useDataset() {
  const [prefix, setPrefix] = useState('')
  const [entries, setEntries] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = async (p = prefix) => {
    setLoading(true)
    setError(null)
    try {
      setEntries(await api.dataset(p))
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return { prefix, setPrefix, entries, error, loading, load }
}

function CaseOptions({ label, setLabel, priority, setPriority, showLabel = true }) {
  return (
    <div className="form-row">
      {showLabel && (
        <label className="field">
          <span>Case label</span>
          <input
            id="case-label"
            className="input"
            maxLength={200}
            placeholder="e.g. Concert 12 Sep, camera 2"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </label>
      )}
      <fieldset className="field">
        <span>Priority</span>
        <div className="segmented">
          <label className={priority === 'normal' ? 'on' : ''}>
            <input type="radio" name="priority" value="normal" checked={priority === 'normal'} onChange={() => setPriority('normal')} />
            Normal
          </label>
          <label className={priority === 'alta' ? 'on' : ''}>
            <input type="radio" name="priority" value="alta" checked={priority === 'alta'} onChange={() => setPriority('alta')} />
            High
          </label>
        </div>
      </fieldset>
    </div>
  )
}

// Operation choice for a file. `value = null` = automatic.
function OperationPicker({ mediaType, value, onChange }) {
  if (!mediaType) {
    return <span className="small tone-text-bad">Unsupported format: it will be created as a failed sub-task</span>
  }
  const selected = value ?? []
  const toggle = (op) => {
    const next = selected.includes(op) ? selected.filter((o) => o !== op) : [...selected, op]
    onChange(next.length ? next : null)
  }
  return (
    <div className="ops">
      {ALLOWED_OPERATIONS[mediaType].map((op) => {
        const on = selected.includes(op)
        const auto = !value && DEFAULT_OPERATIONS[mediaType].includes(op)
        return (
          <button
            key={op}
            type="button"
            className={`op ${on ? 'on' : ''} ${auto ? 'auto' : ''}`}
            onClick={() => toggle(op)}
            title={auto ? 'Would be chosen by the coordinator (automatic)' : ''}
          >
            {OPERATIONS[op]}
          </button>
        )
      })}
      {!value && <span className="small muted">automatic</span>}
    </div>
  )
}

function ResultBanner({ result }) {
  if (!result) return null
  return (
    <div className="banner tone-ok">
      <span>
        Case <b className="mono">{shortId(result.case_id)}</b> created: {result.file_count} files, {result.subtask_count}{' '}
        sub-tasks{result.prefailed_count ? `, ${result.prefailed_count} failed due to unsupported format` : ''}.
      </span>
      <a className="btn btn-small" href={`#/cases/${result.case_id}`}>
        View case
      </a>
    </div>
  )
}

// ---- 1. From the dataset --------------------------------------------------- //

function FromDataset() {
  const ds = useDataset()
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')
  const [selected, setSelected] = useState({}) // input_key -> operations | null
  const [label, setLabel] = useState('')
  const [priority, setPriority] = useState('normal')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (ds.entries ?? []).filter(
      (e) =>
        (typeFilter === 'all' || (e.media_type ?? 'none') === typeFilter) &&
        (!q || e.input_key.toLowerCase().includes(q)),
    )
  }, [ds.entries, query, typeFilter])

  const byKey = useMemo(() => new Map((ds.entries ?? []).map((e) => [e.input_key, e])), [ds.entries])
  const selectedKeys = Object.keys(selected)
  const mix = new Set(selectedKeys.map((k) => byKey.get(k)?.media_type ?? 'none'))

  const toggle = (key) =>
    setSelected((s) => {
      const next = { ...s }
      if (key in next) delete next[key]
      else next[key] = null
      return next
    })

  const allVisibleSelected = visible.length > 0 && visible.every((e) => e.input_key in selected)
  const toggleAll = () =>
    setSelected((s) => {
      const next = { ...s }
      for (const e of visible) {
        if (allVisibleSelected) delete next[e.input_key]
        else if (!(e.input_key in next)) next[e.input_key] = null
      }
      return next
    })

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const res = await api.createCase({
        files: selectedKeys.map((k) => ({ input_key: k, operations: selected[k] })),
        priority,
        label: label.trim() || null,
        source: 'manual',
      })
      navigate(`/cases/${res.case_id}`)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="stack-lg">
      <Section
        title="Dataset files"
        aside={
          <span className="row-gap">
            <input
              id="dataset-prefix"
              className="input"
              placeholder="Prefix (e.g. batch1/)"
              value={ds.prefix}
              onChange={(e) => ds.setPrefix(e.target.value)}
            />
            <button type="button" className="btn btn-small" onClick={() => ds.load()} disabled={ds.loading}>
              {ds.loading ? 'Loading…' : 'List'}
            </button>
          </span>
        }
      >
        <ErrorBanner error={ds.error} onRetry={() => ds.load()} />
        <div className="toolbar">
          <div className="chips">
            {['all', 'video', 'audio', 'image', 'none'].map((t) => (
              <button key={t} type="button" className={`chip ${typeFilter === t ? 'on' : ''}`} onClick={() => setTypeFilter(t)}>
                {t === 'all' ? 'All' : t === 'none' ? 'Unsupported' : MEDIA_TYPES[t]}
              </button>
            ))}
          </div>
          <input
            id="dataset-search"
            className="input search"
            type="search"
            placeholder="Filter by name"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        {ds.entries && visible.length === 0 ? (
          <Empty title="No matching files" />
        ) : (
          <div className="table-wrap scroll-y">
            <table className="table compact">
              <thead>
                <tr>
                  <th className="col-check">
                    <input type="checkbox" aria-label="Select visible" checked={allVisibleSelected} onChange={toggleAll} />
                  </th>
                  <th>File</th>
                  <th>Type</th>
                  <th className="right">Size</th>
                  <th>Metadata</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((e) => (
                  <tr key={e.input_key} className="clickable" onClick={() => toggle(e.input_key)}>
                    <td className="col-check">
                      <input
                        type="checkbox"
                        aria-label={`Select ${e.input_key}`}
                        checked={e.input_key in selected}
                        onChange={() => toggle(e.input_key)}
                        onClick={(ev) => ev.stopPropagation()}
                      />
                    </td>
                    <td className="mono small">{e.input_key}</td>
                    <td>{e.media_type ? MEDIA_TYPES[e.media_type] : <span className="tone-text-bad">Unsupported</span>}</td>
                    <td className="right num small">{formatBytes(e.size_bytes)}</td>
                    <td className="small muted meta-cell">{metaPreview(e.metadata)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section
        title={`Case to submit · ${selectedKeys.length} files`}
        aside={
          selectedKeys.length > 0 && (
            <span className={`pill ${mix.size > 1 ? 'tone-accent' : 'tone-neutral'}`}>
              {mix.size > 1 ? 'Heterogeneous' : 'Homogeneous'}
            </span>
          )
        }
      >
        {selectedKeys.length === 0 ? (
          <p className="muted">Select files in the table above.</p>
        ) : (
          <ul className="picked">
            {selectedKeys.map((k) => (
              <li key={k}>
                <span className="mono small picked-name" title={k}>{basename(k)}</span>
                <OperationPicker
                  mediaType={byKey.get(k)?.media_type ?? detectMediaType(k)}
                  value={selected[k]}
                  onChange={(ops) => setSelected((s) => ({ ...s, [k]: ops }))}
                />
                <button type="button" className="btn-icon" aria-label={`Remove ${k}`} onClick={() => toggle(k)}>
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
        <CaseOptions label={label} setLabel={setLabel} priority={priority} setPriority={setPriority} />
        <ErrorBanner error={error} />
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={busy || selectedKeys.length === 0}>
            {busy ? 'Submitting…' : 'Submit case'}
          </button>
        </div>
      </Section>
    </form>
  )
}

function metaPreview(meta) {
  const entries = Object.entries(meta || {})
  if (!entries.length) return '—'
  return entries
    .slice(0, 3)
    .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`)
    .join(' · ')
}

// ---- 2. Upload files ----------------------------------------------------- //

function FromUpload() {
  const [files, setFiles] = useState([]) // [{ file, ops, progress, status }]
  const [label, setLabel] = useState('')
  const [priority, setPriority] = useState('normal')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  const add = (list) => {
    setResult(null)
    setFiles((prev) => [
      ...prev,
      ...Array.from(list).map((file) => ({ file, ops: null, progress: 0, status: 'ready' })),
    ])
  }

  const patch = (i, changes) => setFiles((prev) => prev.map((f, j) => (j === i ? { ...f, ...changes } : f)))

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      // 1) Presigned PUT URLs (max 200 per request, validated by the coordinator).
      const { urls } = await api.uploadUrls(files.map((f) => f.file.name))
      // 2) Direct upload to S3, in parallel.
      await Promise.all(
        urls.map(async (u, i) => {
          patch(i, { status: 'uploading' })
          await putToS3(u.url, files[i].file, (p) => patch(i, { progress: p }))
          patch(i, { status: 'uploaded', progress: 100 })
        }),
      )
      // 3) Case with the keys just uploaded.
      const res = await api.createCase({
        files: urls.map((u, i) => ({ input_key: u.input_key, operations: files[i].ops })),
        priority,
        label: label.trim() || null,
        source: 'manual',
      })
      setResult(res)
      setFiles([])
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="stack-lg">
      <Section title="Local files">
        <label
          className="dropzone"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault()
            add(e.dataTransfer.files)
          }}
        >
          <input id="upload-files" type="file" multiple onChange={(e) => add(e.target.files)} disabled={busy} />
          <strong>Drag files here or click to choose them</strong>
          <span className="muted small">
            They are uploaded straight to the dataset bucket with a presigned URL; the bytes do not go through the coordinator.
          </span>
        </label>

        {files.length > 0 && (
          <ul className="picked">
            {files.map((f, i) => (
              <li key={`${f.file.name}-${i}`}>
                <span className="mono small picked-name" title={f.file.name}>
                  {f.file.name} <span className="muted">· {formatBytes(f.file.size)}</span>
                </span>
                {busy ? (
                  <div className="mini-progress wide">
                    <span style={{ width: `${f.progress}%` }} className="live" />
                    <small className="num">{f.status} {f.progress}%</small>
                  </div>
                ) : (
                  <OperationPicker mediaType={detectMediaType(f.file.name)} value={f.ops} onChange={(ops) => patch(i, { ops })} />
                )}
                {!busy && (
                  <button
                    type="button"
                    className="btn-icon"
                    aria-label={`Remove ${f.file.name}`}
                    onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                  >
                    ×
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
        <CaseOptions label={label} setLabel={setLabel} priority={priority} setPriority={setPriority} />
        <ErrorBanner error={error} />
        <ResultBanner result={result} />
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={busy || files.length === 0 || files.length > 200}>
            {busy ? 'Uploading and submitting…' : `Upload ${files.length} and submit case`}
          </button>
          {files.length > 200 && <span className="small tone-text-bad">Maximum 200 files per upload.</span>}
        </div>
      </Section>
    </form>
  )
}

// ---- 3. Auto-generation --------------------------------------------- //

function AutoGenerate() {
  const ds = useDataset()
  const [criterion, setCriterion] = useState('folder')
  const [metaKey, setMetaKey] = useState('')
  const [priority, setPriority] = useState('normal')
  const [busy, setBusy] = useState(false)
  const [results, setResults] = useState([])

  const metaKeys = useMemo(() => {
    const keys = new Set()
    for (const e of ds.entries ?? []) Object.keys(e.metadata || {}).forEach((k) => keys.add(k))
    return [...keys].sort()
  }, [ds.entries])

  const groups = useMemo(() => {
    const map = new Map()
    for (const e of ds.entries ?? []) {
      let key
      if (criterion === 'folder') {
        const i = e.input_key.lastIndexOf('/')
        key = i >= 0 ? e.input_key.slice(0, i + 1) : '(root)'
      } else {
        const v = e.metadata?.[metaKey]
        if (v == null) continue
        key = `${metaKey} = ${typeof v === 'object' ? JSON.stringify(v) : v}`
      }
      if (!map.has(key)) map.set(key, [])
      map.get(key).push(e)
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [ds.entries, criterion, metaKey])

  const run = async () => {
    setBusy(true)
    setResults(groups.map(([name]) => ({ name, state: 'sending' })))
    // All cases are sent at once so the concurrency between cases is visible.
    await Promise.all(
      groups.map(async ([name, entries], i) => {
        try {
          const res = await api.createCase({
            files: entries.slice(0, 1000).map((e) => ({ input_key: e.input_key })),
            priority,
            label: `auto · ${name}`,
            source: 'auto',
          })
          setResults((r) => r.map((x, j) => (j === i ? { name, state: 'ok', res } : x)))
        } catch (err) {
          setResults((r) => r.map((x, j) => (j === i ? { name, state: 'error', error: err.message } : x)))
        }
      }),
    )
    setBusy(false)
  }

  return (
    <div className="stack-lg">
      <Section title="Grouping criterion">
        <p className="muted">
          Groups the dataset files and creates one case per group, all at the same time. The coordinator decides each
          file's operations based on its type.
        </p>
        <div className="form-row">
          <fieldset className="field">
            <span>Group by</span>
            <div className="segmented">
              <label className={criterion === 'folder' ? 'on' : ''}>
                <input type="radio" name="criterion" checked={criterion === 'folder'} onChange={() => setCriterion('folder')} />
                Folder
              </label>
              <label className={criterion === 'meta' ? 'on' : ''}>
                <input
                  type="radio"
                  name="criterion"
                  checked={criterion === 'meta'}
                  onChange={() => setCriterion('meta')}
                  disabled={!metaKeys.length}
                />
                Metadata field
              </label>
            </div>
          </fieldset>
          {criterion === 'meta' && (
            <label className="field">
              <span>Field</span>
              <select id="meta-key" className="input" value={metaKey} onChange={(e) => setMetaKey(e.target.value)}>
                <option value="">Choose…</option>
                {metaKeys.map((k) => (
                  <option key={k} value={k}>{k}</option>
                ))}
              </select>
            </label>
          )}
          <label className="field">
            <span>Prefix</span>
            <span className="row-gap">
              <input
                id="auto-prefix"
                className="input"
                placeholder="entire bucket"
                value={ds.prefix}
                onChange={(e) => ds.setPrefix(e.target.value)}
              />
              <button type="button" className="btn btn-small" onClick={() => ds.load()} disabled={ds.loading}>
                {ds.loading ? 'Loading…' : 'List'}
              </button>
            </span>
          </label>
        </div>
        <CaseOptions showLabel={false} priority={priority} setPriority={setPriority} />
        <ErrorBanner error={ds.error} onRetry={() => ds.load()} />
      </Section>

      <Section
        title={`Preview · ${groups.length} cases`}
        aside={
          <button type="button" className="btn btn-primary" onClick={run} disabled={busy || groups.length === 0}>
            {busy ? 'Sending…' : `Create ${groups.length} cases in parallel`}
          </button>
        }
      >
        {groups.length === 0 ? (
          <Empty title={ds.entries ? 'No groups for this criterion' : 'Loading dataset…'} />
        ) : (
          <div className="table-wrap scroll-y">
            <table className="table compact">
              <thead>
                <tr>
                  <th>Group</th>
                  <th className="right">Files</th>
                  <th>Composition</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {groups.map(([name, entries], i) => {
                  const r = results[i]?.name === name ? results[i] : null
                  return (
                    <tr key={name}>
                      <td className="mono small">{name}</td>
                      <td className="right num">{entries.length}</td>
                      <td className="small">{composition(entries)}</td>
                      <td className="small">
                        {!r && <span className="muted">—</span>}
                        {r?.state === 'sending' && <span className="muted">sending…</span>}
                        {r?.state === 'ok' && (
                          <a href={`#/cases/${r.res.case_id}`}>
                            {shortId(r.res.case_id)} · {r.res.subtask_count} sub-tasks
                          </a>
                        )}
                        {r?.state === 'error' && <span className="tone-text-bad">{r.error}</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  )
}

function composition(entries) {
  const count = {}
  for (const e of entries) {
    const t = e.media_type ?? 'none'
    count[t] = (count[t] || 0) + 1
  }
  const parts = Object.entries(count).map(([t, n]) => `${n} ${t === 'none' ? 'unsupported' : MEDIA_TYPES[t].toLowerCase()}`)
  return `${parts.join(' · ')}${Object.keys(count).length > 1 ? ' — heterogeneous' : ' — homogeneous'}`
}
