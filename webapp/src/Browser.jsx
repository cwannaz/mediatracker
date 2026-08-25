import { useEffect, useState, useCallback } from 'react'
import ArticleView from './ArticleView.jsx'
import CommenterView from './CommenterView.jsx'
import PersonaView from './PersonaView.jsx'
import Aggregation from './Aggregation.jsx'
import Population from './Population.jsx'

const SUBTABS = [
  { id: 'today', label: 'Today', statKey: 'today' },
  { id: 'articles', label: 'Articles', statKey: 'articles' },
  { id: 'commenters', label: 'Commenters', statKey: 'commenters' },
  { id: 'people', label: 'People', statKey: 'personas' },
  { id: 'aggregation', label: 'Aggregation' },
  { id: 'population', label: 'Population', statKey: 'profiles' },
  { id: 'authors', label: 'Authors', statKey: 'authors' },
  { id: 'sources', label: 'Sources', statKey: 'sources' },
]

const fmtDate = (v) => {
  if (!v) return '—'
  try { return new Date(v).toLocaleDateString() } catch { return String(v).slice(0, 10) }
}

// The papers' own timezone. The daemon selects "today" on a Zurich calendar
// day, so the list must be labelled and timed in that zone too — a reader in
// another one would otherwise see hours that contradict the heading.
const PAPER_TZ = 'Europe/Zurich'

// Within a single day the date is the same on every row, so the hour is the
// only part that tells the reader anything.
const fmtTime = (v) => {
  if (!v) return '—'
  try {
    return new Date(v).toLocaleTimeString(undefined,
      { timeZone: PAPER_TZ, hour: '2-digit', minute: '2-digit' })
  } catch { return '—' }
}

// Today's date in Zurich, as YYYY-MM-DD, to compare against the day the
// daemon says it served.
const paperToday = () => {
  try {
    return new Intl.DateTimeFormat('en-CA', { timeZone: PAPER_TZ }).format(new Date())
  } catch { return '' }
}

// A YYYY-MM-DD read as a plain calendar date, not as an instant — pinned to
// midday UTC so no timezone can shift it onto the neighbouring day.
const fmtDay = (iso) => {
  if (!iso) return ''
  try {
    return new Date(`${iso}T12:00:00Z`).toLocaleDateString(undefined,
      { timeZone: 'UTC', weekday: 'long', day: 'numeric', month: 'long' })
  } catch { return iso }
}

export default function Browser({ connected, send, route, navigate, back }) {
  const [stats, setStats] = useState(null)

  // The subtab and the open entity both come from the URL, so opening an
  // article and pressing Back returns to the list it was opened from.
  const tab = SUBTABS.some((t) => t.id === route[0]) ? route[0] : SUBTABS[0].id
  const open = route[1] || null

  useEffect(() => {
    if (!connected) return
    send('dataset_stats').then((r) => { if (r.ok) setStats(r) }).catch(() => {})
  }, [connected, send])

  const showNick = useCallback((nick) => navigate(['commenters', nick]), [navigate])
  const showPersona = useCallback((id) => navigate(['people', id]), [navigate])
  const showArticle = useCallback((id) => navigate(['articles', id]), [navigate])
  const openHere = useCallback((id) => navigate([tab, id]), [navigate, tab])
  // Closing a detail view walks the history back, so the app's Back button and
  // the browser's do the same thing.
  const close = useCallback(() => back([tab]), [back, tab])

  return (
    <>
      <nav className="subtabs" role="tablist" aria-label="Dataset categories">
        {SUBTABS.map((t) => (
          <button key={t.id} className="subtab" role="tab"
            aria-selected={tab === t.id}
            onClick={() => navigate([t.id])}>
            {t.label}
            {stats?.[t.statKey] != null && <span className="count">{stats[t.statKey]}</span>}
          </button>
        ))}
      </nav>

      <div className="browser">
        {!connected && <div className="empty">Daemon offline.</div>}

        {connected && (tab === 'today' || tab === 'articles') && (
          open
            ? <ArticleView articleId={open} send={send}
                onBack={close} onNick={showNick} />
            : <ArticleList send={send} onOpen={openHere}
                day={tab === 'today' ? 'today' : null} />
        )}

        {connected && tab === 'commenters' && (
          open
            ? <CommenterView nick={open} send={send}
                onBack={close} onPersona={showPersona} onArticle={showArticle} />
            : <CommenterList send={send} onOpen={openHere} />
        )}

        {connected && tab === 'people' && (
          open
            ? <PersonaView personaId={open} send={send}
                onBack={close} onNick={showNick} onArticle={showArticle} />
            : <PersonaList send={send} onOpen={openHere} />
        )}

        {connected && tab === 'aggregation' && <Aggregation send={send} onPersona={showPersona} />}

        {connected && tab === 'population' && (
          <Population send={send} onNick={showNick} onPersona={showPersona} />
        )}

        {connected && tab === 'authors' && <AuthorList send={send} />}
        {connected && tab === 'sources' && <SourceList send={send} />}
      </div>
    </>
  )
}

function useQuery(send, cmd, key, params = {}) {
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState(null)
  const [resp, setResp] = useState(null)   // the rest of the reply, for callers that need it
  const dep = JSON.stringify(params)
  useEffect(() => {
    let alive = true
    send(cmd, params).then((r) => {
      if (!alive) return
      setResp(r)
      if (r.ok) { setRows(r[key] || []); setErr(null) } else { setErr(r.error); setRows([]) }
    }).catch(() => {})
    return () => { alive = false }
  }, [cmd, key, dep])  // eslint-disable-line
  return [rows, err, resp]
}

// `day` restricts the list to one publication date, resolved on the daemon in
// the papers' own timezone. The Today subtab passes 'today'; the Articles
// subtab passes nothing and lists the whole corpus.
function ArticleList({ send, onOpen, day = null }) {
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  const [rows, err, resp] = useQuery(send, 'browse_articles', 'articles',
    { q: term || null, day, limit: 300 })
  // Which day the daemon settled on. Between midnight and the papers' first
  // article of the morning it is not the current one, and saying so is the
  // whole point — an unlabelled "Today" that quietly shows yesterday would be
  // worse than an empty one.
  const shown = resp?.day || null
  const stale = shown && shown !== paperToday()
  const empty = day ? 'No articles collected yet.' : 'No articles.'
  return (
    <>
      <div className="toolbar">
        <input type="text" placeholder="Search headlines (regex, e.g. ^Les|Syrie)" value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && setTerm(q)} />
        <button className="btn secondary" onClick={() => setTerm(q)}>Search</button>
        {day && shown && <span className="subtle">
          {stale
            ? `Nothing published yet on ${fmtDay(paperToday())} — showing ${fmtDay(shown)}, the most recent day with articles`
            : `Published ${fmtDay(shown)}, Swiss time`}
        </span>}
      </div>
      {err && <div className="banner warn">{err}</div>}
      {!rows ? <div className="empty">Loading…</div> : rows.length === 0 ? <div className="empty">{empty}</div> : (
        <div className="table-wrap"><table>
          <thead><tr>
            <th>{day ? 'Time' : 'Published'}</th><th>Headline</th><th>Journal</th><th>Section</th>
            <th>Author</th><th>Source</th><th className="num">Comments</th>
          </tr></thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.id} className="rowlink" onClick={() => onOpen(a.id)}>
                <td>{day ? fmtTime(a.published_at) : fmtDate(a.published_at)}</td>
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
  const [rows, err] = useQuery(send, 'browse_commenters', 'commenters', { q: term || null, limit: 500 })
  return (
    <>
      <div className="toolbar">
        <input type="text" placeholder="Search nicknames (regex, e.g. ^j|_64$)" value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && setTerm(q)} />
        <button className="btn secondary" onClick={() => setTerm(q)}>Search</button>
      </div>
      {err && <div className="banner warn">{err}</div>}
      {!rows ? <div className="empty">Loading…</div> : (
        <div className="table-wrap"><table>
          <thead><tr>
            <th>Nickname</th><th>Person</th><th className="num">Comments</th><th className="num">Articles</th>
            <th>First seen</th><th>Last seen</th><th className="num">Journals</th><th className="num">Votes</th>
          </tr></thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.nick} className="rowlink" onClick={() => onOpen(c.nick)}>
                <td><strong>{c.nick}</strong></td>
                <td>{c.persona_label || <span className="subtle">—</span>}</td>
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

function PersonaList({ send, onOpen }) {
  const [rows] = useQuery(send, 'list_personas', 'personas', {})
  if (!rows) return <div className="empty">Loading…</div>
  if (!rows.length) return (
    <div className="empty">
      No people defined yet. Open a commenter and use the Identity card to link
      the nicknames that belong to the same person.
    </div>
  )
  return (
    <div className="table-wrap"><table>
      <thead><tr>
        <th>Person</th><th className="num">Aliases</th><th className="num">Comments</th>
        <th className="num">Articles</th><th>First seen</th><th>Last seen</th>
        <th className="num">Journals</th><th className="num">Votes</th>
      </tr></thead>
      <tbody>{rows.map((p) => (
        <tr key={p.id} className="rowlink" onClick={() => onOpen(p.id)}>
          <td><strong>{p.label}</strong>
            <div className="subtle">{(p.aliases || []).join(', ')}</div></td>
          <td className="num">{p.n_aliases}</td>
          <td className="num">{p.comments}</td>
          <td className="num">{p.articles}</td>
          <td>{fmtDate(p.first_seen)}</td>
          <td>{fmtDate(p.last_seen)}</td>
          <td className="num">{p.journals}</td>
          <td className="num">{p.total_votes ?? '—'}</td>
        </tr>
      ))}</tbody>
    </table></div>
  )
}

function AuthorList({ send }) {
  const [rows] = useQuery(send, 'browse_authors', 'authors', { limit: 500 })
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
  const [rows] = useQuery(send, 'browse_sources', 'sources', { limit: 500 })
  if (!rows) return <div className="empty">Loading…</div>
  if (!rows.length) return <div className="empty">No news agencies recorded yet.</div>
  return (
    <div className="table-wrap"><table>
      <thead><tr><th>Source / agency</th><th className="num">Articles</th><th>First</th><th>Last</th></tr></thead>
      <tbody>{rows.map((s) => (
        <tr key={s.source}>
          <td><strong>{s.source}</strong>
            {(s.variants || []).length > 0 && (
              <div className="subtle">also bylined: {s.variants.join(', ')}</div>
            )}</td>
          <td className="num">{s.articles}</td>
          <td>{fmtDate(s.first_seen)}</td><td>{fmtDate(s.last_seen)}</td>
        </tr>
      ))}</tbody>
    </table></div>
  )
}
