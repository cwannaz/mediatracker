import { useEffect, useState } from 'react'

const fmt = (iso) => {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

export default function SourcePanel({ source, send, status, bump, onSaved }) {
  const sched = source.schedule || {}
  const [baseUrl, setBaseUrl] = useState(sched.base_url ?? source.base_url ?? '')
  const [startLocal, setStartLocal] = useState(sched.scan_start_local ?? '06:00')
  const [periodH, setPeriodH] = useState(sched.scan_period_hours ?? 4)
  const [variabilityH, setVariabilityH] = useState(sched.scan_variability_hours ?? 0.5)
  const [enabled, setEnabled] = useState(sched.enabled !== false)
  const [tz] = useState(sched.timezone ?? 'Europe/Zurich')
  const [saved, setSaved] = useState(false)
  const [history, setHistory] = useState([])
  const [busy, setBusy] = useState(false)

  const loadHistory = async () => {
    try {
      const r = await send('scan_history', { journal: source.slug, limit: 50 })
      if (r.ok) setHistory(r.runs)
    } catch { /* ignore */ }
  }
  useEffect(() => { loadHistory() }, [source.slug, bump])

  const save = async () => {
    setBusy(true)
    try {
      const r = await send('update_source', {
        journal: source.slug,
        schedule: {
          enabled,
          base_url: baseUrl || null,
          scan_start_local: startLocal,
          scan_period_hours: Number(periodH),
          scan_variability_hours: Number(variabilityH),
          timezone: tz,
        },
      })
      if (r.ok) { setSaved(true); setTimeout(() => setSaved(false), 2000); onSaved?.() }
    } finally { setBusy(false) }
  }

  const scanNow = async () => {
    setBusy(true)
    try { await send('trigger_scan', { journal: source.slug }) }
    finally { setBusy(false) }
  }

  const running = status.current?.slug === source.slug ? status.current : null
  const total = running?.total
  const done = running?.current ?? 0
  const pct = total ? Math.round((done / total) * 100) : (running ? 8 : 0)

  return (
    <>
      <div className="panel-head">
        <h1>{source.name}</h1>
        <span className="slug">{source.slug}</span>
      </div>
      <p className="subtle">
        Next scheduled scan: {source.schedule?.enabled === false ? 'disabled' : fmt(source.next_scan_at)}
        {status.queued > 0 && ` · ${status.queued} queued`}
        {!source.comments_supported &&
          ' · comments not yet enabled for this source (tenantId pending)'}
      </p>

      <div className="card">
        <h2>Schedule</h2>
        <div className="form-grid">
          <label>Base URL</label>
          <input type="text" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />

          <label>Start time ({tz})</label>
          <input type="time" value={startLocal} onChange={(e) => setStartLocal(e.target.value)} />

          <label>Period (hours)</label>
          <input type="number" min="0.25" step="0.25" value={periodH}
            onChange={(e) => setPeriodH(e.target.value)} />
          <div className="hint">Re-scan cadence through the day (e.g. 4 → 06:00, 10:00, 14:00…).</div>

          <label>Variability (hours)</label>
          <input type="number" min="0" step="0.25" value={variabilityH}
            onChange={(e) => setVariabilityH(e.target.value)} />
          <div className="hint">Random ± jitter applied to each scheduled scan so access looks human. Fractional allowed.</div>

          <label>Enabled</label>
          <div className="checkbox">
            <input id="en" type="checkbox" checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)} />
            <label htmlFor="en" style={{ color: 'var(--text)' }}>Run scheduled scans</label>
          </div>
        </div>
        <div className="actions">
          <button className="btn" onClick={save} disabled={busy}>Save schedule</button>
          <button className="btn secondary" onClick={scanNow} disabled={busy || !!running}>
            {running ? 'Scanning…' : 'Scan now'}
          </button>
          {saved && <span className="saved">✓ saved</span>}
        </div>

        {running && (
          <div className="progress-wrap">
            <div className="progress"><span style={{ width: `${pct}%` }} /></div>
            <div className="progress-label">
              {running.phase === 'scanning' && total != null
                ? `Scanning article ${done} / ${total}`
                : `Starting scan (${running.trigger})…`}
            </div>
          </div>
        )}
      </div>

      <div className="card">
        <h2>Recent scans</h2>
        {history.length === 0 ? (
          <div className="empty">No scans yet. Use “Scan now” to run one.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Requested</th><th>Trigger</th><th>Status</th>
                  <th className="num">Articles</th><th className="num">Art. snaps</th>
                  <th className="num">Comments</th><th className="num">Cmt snaps</th>
                  <th className="num">Errors</th>
                </tr>
              </thead>
              <tbody>
                {history.map((r) => (
                  <tr key={r.id}>
                    <td>{fmt(r.requested_at)}</td>
                    <td>{r.trigger}</td>
                    <td><span className={`badge ${r.status}`}>{r.status}</span></td>
                    <td className="num">{r.articles_seen}</td>
                    <td className="num">{r.article_snapshots}</td>
                    <td className="num">{r.comments_seen}</td>
                    <td className="num">{r.comment_snapshots}</td>
                    <td className="num">{r.errors}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
