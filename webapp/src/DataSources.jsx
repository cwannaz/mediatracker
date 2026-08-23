import { useCallback, useEffect, useRef, useState } from 'react'
import SourcePanel from './SourcePanel.jsx'

export default function DataSources({ connected, send }) {
  const [sources, setSources] = useState([])
  const [selected, setSelected] = useState(null)
  const [status, setStatus] = useState({ current: null, queued: 0, last_stats: {} })
  const [degraded, setDegraded] = useState(false)
  const [bump, setBump] = useState(0)   // bumped when a scan completes -> reload history
  const prevRunning = useRef(null)

  const loadSources = useCallback(async () => {
    try {
      const r = await send('list_sources')
      if (r.ok) {
        setSources(r.sources)
        setSelected((s) => s ?? r.sources[0]?.slug ?? null)
      }
      const st = await send('status')
      if (st.ok) setDegraded(st.degraded)
    } catch { /* not connected */ }
  }, [send])

  // Initial + on-connect load.
  useEffect(() => { if (connected) loadSources() }, [connected, loadSources])

  // Poll scan progress; detect scan completion to refresh sources + history.
  useEffect(() => {
    if (!connected) return
    let alive = true
    const poll = async () => {
      try {
        const r = await send('scan_status')
        if (alive && r.ok) {
          setStatus(r)
          const running = r.current?.slug ?? null
          if (prevRunning.current && prevRunning.current !== running) {
            setBump((b) => b + 1)     // a scan just finished
            loadSources()
          }
          prevRunning.current = running
        }
      } catch { /* ignore */ }
    }
    poll()
    const t = setInterval(poll, 900)
    return () => { alive = false; clearInterval(t) }
  }, [connected, send, loadSources])

  const selectedSource = sources.find((s) => s.slug === selected) || null

  return (
    <>
      {degraded && (
        <div className="banner warn">
          Postgres unavailable — schedules and history are disabled (running on the JSONL fallback).
        </div>
      )}
      <div className="shell">
        <nav className="tabrail" role="tablist" aria-orientation="vertical" aria-label="Data sources">
          <div className="tabrail-group">Data sources</div>
          {sources.map((s) => (
            <button
              key={s.slug}
              className="tabrail-item"
              role="tab"
              aria-selected={selected === s.slug}
              onClick={() => setSelected(s.slug)}
            >
              {s.name}
              <span className="sub">
                {status.current?.slug === s.slug
                  ? 'scanning…'
                  : (s.schedule?.enabled === false ? 'disabled' : s.slug)}
              </span>
            </button>
          ))}
          {sources.length === 0 && <div className="empty" style={{ padding: '8px 10px' }}>
            {connected ? 'Loading…' : 'Daemon offline'}
          </div>}
        </nav>

        <main className="work">
          {selectedSource ? (
            <SourcePanel
              key={selectedSource.slug}
              source={selectedSource}
              send={send}
              status={status}
              bump={bump}
              onSaved={loadSources}
            />
          ) : (
            <div className="empty">Select a data source.</div>
          )}
        </main>
      </div>
    </>
  )
}
