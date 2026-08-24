import { useEffect, useState, useCallback } from 'react'
import ArticleView from './ArticleView.jsx'
import CommenterView from './CommenterView.jsx'

const SUBTABS = [
  { id: 'articles', label: 'Articles', statKey: 'articles' },
  { id: 'commenters', label: 'Commenters', statKey: 'commenters' },
  { id: 'authors', label: 'Authors', statKey: 'authors' },
  { id: 'sources', label: 'Sources', statKey: 'sources' },
]

const fmtDate = (v) => {
  if (!v) return '—'
  try { return new Date(v).toLocaleDateString() } catch { return String(v).slice(0, 10) }
}

export default function Browser({ connected, send }) {
  const [tab, setTab] = useState('articles')
  const [stats, setStats] = useState(null)
  // Selected entity opens a detail view; null shows the list.
  const [openArticle, setOpenArticle] = useState(null)
  const [openNick, setOpenNick] = useState(null)

  useEffect(() => {
    if (!connected) return
    send('dataset_stats').then((r) => { if (r.ok) setStats(r) }).catch(() => {})
  }, [connected, send])

  const showNick = useCallback((nick) => { setTab('commenters'); setOpenNick(nick) }, [])

  return (
    <>
      <nav className="subtabs" role="tablist" aria-label="Dataset categories">
        {SUBTABS.map((t) => (
          <button key={t.id} className="subtab" role="tab"
            aria-selected={tab === t.id}
            onClick={() => { setTab(t.id); setOpenArticle(null); setOpenNick(null) }}>
            {t.label}
            {stats?.[t.statKey] != null && <span className="count">{stats[t.statKey]}</span>}
          </button>
        ))}
      </nav>

      <div className="browser">
        {!connected && <div className="empty">Daemon offline.</div>}

        {connected && tab === 'articles' && (
          openArticle
            ? <ArticleView articleId={openArticle} send={send}
                onBack={() => setOpenArticle(null)} onNick={showNick} />
            : <ArticleList send={send} onOpen={setOpenArticle} />
        )}

        {connected && tab === 'commenters' && (
          openNick
            ? <CommenterView nick={openNick} send={send}
                onBack={() => setOpenNick(null)} onArticle={(id) => { setTab('articles'); setOpenArticle(id) }} />
            : <CommenterList send={send} onOpen={setOpenNick} />
        )}

        {connected && tab === 'authors' && <AuthorList send={send} />}
        {connected && tab === 'sources' && <SourceList send={send} />}
      </div>
    </>
  )
}

function useQuery(send, cmd, key, params = {}) {
  const [rows, setRows] = useState(null)
  const dep = JSON.stringify(params)
  useEffect(() => {
    let alive = true
    send(cmd, params).then((r) => { if (alive && r.ok) setRows(r[key] || []) }).catch(() => {})
    return () => { alive = false }
  }, [cmd, key, dep])  // eslint-disable-line
  return rows
}

function ArticleList({ send, onOpen }) {
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  const rows = useQuery(send, 'browse_articles', 'articles', { q: term || null, limit: 300 })
  return (
    <>
      <div className="toolbar">
        <input type="text" placeholder="Search headlines…" value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && setTerm(q)} />
        <button className="btn secondary" onClick={() => setTerm(q)}>Search</button>
      </div>
      {!rows ? <div className="empty">Loading…</div> : rows.length === 0 ? <div className="empty">No articles.</div> : (
        <div className="table-wrap"><table>
          <thead><tr>
            <th>Published</th><th>Headline</th><th>Journal</th><th>Section</th>
            <th>Author</th><th>Source</th><th className="num">Comments</th>
          </tr></thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.id} className="rowlink" onClick={() => onOpen(a.id)}>
                <td>{fmtDate(a.published_at)}</td>
                <td style={{ whiteSpace: 'normal', maxWidth: 460 }}>
                  {a.headline || '(untitled)'}
                  {a.origin === 'pdf' && <span className="chip pdf">archive</span>}
                </td>
                <td>{a.journal}</td><td>{a.section || '—'}</td>
                <td>{a.author || '—'}</td><td>{a.source || '—'}</td>
                <td className="num">{a.comment_count ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </>
  )
}

function CommenterList({ send, onOpen }) {
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  const rows = useQuery(send, 'browse_commenters', 'commenters', { q: term || null, limit: 500 })
  return (
    <>
      <div className="toolbar">
        <input type="text" placeholder="Search nicknames…" value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && setTerm(q)} />
        <button className="btn secondary" onClick={() => setTerm(q)}>Search</button>
      </div>
      {!rows ? <div className="empty">Loading…</div> : (
        <div className="table-wrap"><table>
          <thead><tr>
            <th>Nickname</th><th className="num">Comments</th><th className="num">Articles</th>
            <th>First seen</th><th>Last seen</th><th className="num">Journals</th><th className="num">Votes</th>
          </tr></thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.nick} className="rowlink" onClick={() => onOpen(c.nick)}>
                <td><strong>{c.nick}</strong></td>
                <td className="num">{c.comments}</td>
                <td className="num">{c.articles}</td>
                <td>{fmtDate(c.first_seen)}</td>
                <td>{fmtDate(c.last_seen)}</td>
                <td className="num">{c.journals}</td>
                <td className="num">{c.total_votes ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </>
  )
}

function AuthorList({ send }) {
  const rows = useQuery(send, 'browse_authors', 'authors', { limit: 500 })
  if (!rows) return <div className="empty">Loading…</div>
  if (!rows.length) return <div className="empty">No bylines recorded yet.</div>
  return (
    <div className="table-wrap"><table>
      <thead><tr><th>Author</th><th className="num">Articles</th><th>First</th><th>Last</th><th className="num">Journals</th></tr></thead>
      <tbody>{rows.map((a) => (
        <tr key={a.author}>
          <td>{a.author}</td><td className="num">{a.articles}</td>
          <td>{fmtDate(a.first_seen)}</td><td>{fmtDate(a.last_seen)}</td>
          <td className="num">{a.journals}</td>
        </tr>
      ))}</tbody>
    </table></div>
  )
}

function SourceList({ send }) {
  const rows = useQuery(send, 'browse_sources', 'sources', { limit: 500 })
  if (!rows) return <div className="empty">Loading…</div>
  if (!rows.length) return <div className="empty">No news agencies recorded yet.</div>
  return (
    <div className="table-wrap"><table>
      <thead><tr><th>Source / agency</th><th className="num">Articles</th><th>First</th><th>Last</th></tr></thead>
      <tbody>{rows.map((s) => (
        <tr key={s.source}>
          <td>{s.source}</td><td className="num">{s.articles}</td>
          <td>{fmtDate(s.first_seen)}</td><td>{fmtDate(s.last_seen)}</td>
        </tr>
      ))}</tbody>
    </table></div>
  )
}
