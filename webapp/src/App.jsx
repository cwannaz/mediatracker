import { useEffect, useState } from 'react'
import { useDaemon } from './useDaemon.js'

// Foundation GUI: connects to the daemon and shows live status/health plus a
// per-journal ingest trigger. The article/thread reproduction views and the
// author-linkage analysis will be added once the read API and adapters land.
export default function App() {
  const { connected, send } = useDaemon()
  const [status, setStatus] = useState(null)
  const [health, setHealth] = useState(null)
  const [busy, setBusy] = useState(null)

  const refresh = async () => {
    try {
      setStatus(await send('status'))
      setHealth(await send('health'))
    } catch { /* not connected yet */ }
  }

  useEffect(() => {
    if (!connected) return
    refresh()
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [connected])

  const ingest = async (journal) => {
    setBusy(journal)
    try { await send('ingest_now', { journal }); await refresh() }
    finally { setBusy(null) }
  }

  return (
    <div className="wrap">
      <header>
        <h1>MediaTracker</h1>
        <span className={connected ? 'pill ok' : 'pill down'}>
          {connected ? 'daemon connected' : 'daemon offline'}
        </span>
      </header>

      {status?.degraded && (
        <div className="banner warn">
          Running degraded — Postgres unavailable, writing to the JSONL fallback store.
        </div>
      )}

      <section>
        <h2>Journals</h2>
        {!status && <p className="muted">Waiting for the daemon…</p>}
        <ul className="journals">
          {(status?.journals ?? []).map((j) => {
            const s = status?.last_stats?.[j]
            return (
              <li key={j}>
                <div className="jhead">
                  <strong>{j}</strong>
                  <button disabled={busy === j} onClick={() => ingest(j)}>
                    {busy === j ? 'ingesting…' : 'Ingest now'}
                  </button>
                </div>
                <div className="muted small">
                  {s
                    ? `articles ${s.articles_seen} · snapshots ${s.article_snapshots} · comments ${s.comments_seen} · new images ${s.images_new} · errors ${s.errors}`
                    : 'no ingest yet'}
                </div>
              </li>
            )
          })}
        </ul>
      </section>

      <section>
        <h2>Health</h2>
        {health
          ? <pre className="health">{JSON.stringify(health, null, 2)}</pre>
          : <p className="muted">—</p>}
      </section>

      <footer className="muted small">
        Article reproduction &amp; author-linkage views coming next.
      </footer>
    </div>
  )
}
