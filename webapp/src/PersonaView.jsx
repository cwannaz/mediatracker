import { useEffect, useMemo, useState } from 'react'
import { languageMetrics } from './textmetrics.js'

const fmt = (v) => { try { return new Date(v).toLocaleString() } catch { return String(v) } }
const fmtD = (v) => { if (!v) return '—'; try { return new Date(v).toLocaleDateString() } catch { return String(v) } }

// A persona is one person behind several nicknames. Everything here is the
// MERGED view across its aliases — that is the point of the grouping.
export default function PersonaView({ personaId, send, onBack, onNick, onArticle }) {
  const [p, setP] = useState(null)
  const [busy, setBusy] = useState(false)
  const [shown, setShown] = useState(150)

  const load = () => send('get_persona', { persona_id: personaId, limit: 3000 })
    .then((r) => { if (r.ok) setP(r.persona) }).catch(() => {})
  useEffect(() => { load() }, [personaId]) // eslint-disable-line

  const comments = p?.comments_list || []
  const withText = comments.filter((c) => c.body_text)
  const metrics = useMemo(() => languageMetrics(withText.map((c) => c.body_text)), [p]) // eslint-disable-line

  const months = useMemo(() => {
    const m = new Map()
    for (const c of comments) {
      if (!c.posted_at) continue
      const k = String(c.posted_at).slice(0, 7)
      m.set(k, (m.get(k) || 0) + 1)
    }
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [p]) // eslint-disable-line

  // Which alias was in use in each month — shows renames as a handover.
  const aliasSpans = useMemo(() => {
    const m = new Map()
    for (const c of comments) {
      if (!c.posted_at || !c.author_nick) continue
      const cur = m.get(c.author_nick) || { first: c.posted_at, last: c.posted_at, n: 0 }
      if (c.posted_at < cur.first) cur.first = c.posted_at
      if (c.posted_at > cur.last) cur.last = c.posted_at
      cur.n++
      m.set(c.author_nick, cur)
    }
    return [...m.entries()].sort((a, b) => String(a[1].first).localeCompare(String(b[1].first)))
  }, [p]) // eslint-disable-line

  if (!p) return <><button className="backlink" onClick={onBack}>← Back</button><div className="empty">Loading…</div></>

  const peak = Math.max(1, ...months.map(([, n]) => n))

  const unlink = async (nick) => {
    setBusy(true)
    try { await send('remove_alias', { nick }); await load() } finally { setBusy(false) }
  }

  return (
    <>
      <button className="backlink" onClick={onBack}>← Back to people</button>
      <div className="panel-head">
        <h1>{p.label}</h1>
        <span className="slug">{p.aliases.length} aliases · {p.comments} comments merged</span>
      </div>
      {p.note && <p className="subtle">{p.note}</p>}

      <div className="card">
        <h2>Aliases</h2>
        <div className="table-wrap"><table>
          <thead><tr>
            <th>Nickname</th><th>Comments</th><th>First</th><th>Last</th>
            <th>Confidence</th><th>Added by</th><th>Evidence</th><th></th>
          </tr></thead>
          <tbody>
            {p.alias_rows.map((a) => {
              const span = aliasSpans.find(([n]) => n === a.nick)?.[1]
              return (
                <tr key={a.nick}>
                  <td><span className="nick" onClick={() => onNick?.(a.nick)}>{a.nick}</span></td>
                  <td className="num">{span?.n ?? 0}</td>
                  <td>{fmtD(span?.first)}</td>
                  <td>{fmtD(span?.last)}</td>
                  <td><span className={`chip ${a.confidence === 'confirmed' ? '' : 'pdf'}`}>{a.confidence}</span></td>
                  <td>{a.added_by}</td>
                  <td style={{ whiteSpace: 'normal', maxWidth: 260 }}>{a.evidence || '—'}</td>
                  <td><button className="iconbtn" disabled={busy} onClick={() => unlink(a.nick)}>unlink</button></td>
                </tr>
              )
            })}
          </tbody>
        </table></div>
      </div>

      <div className="card">
        <h2>Merged metadata</h2>
        <div className="metrics">
          <Metric k="Comments" v={p.comments} />
          <Metric k="Articles" v={p.articles} />
          <Metric k="Journals" v={p.journals} />
          <Metric k="First seen" v={fmtD(p.first_seen)} />
          <Metric k="Last seen" v={fmtD(p.last_seen)} />
          <Metric k="Votes received" v={p.total_votes ?? '—'} />
        </div>
      </div>

      <div className="card">
        <h2>Timeline — comments per month (all aliases)</h2>
        {months.length === 0 ? <div className="empty">No dated comments.</div> : (
          <>
            <div className="timeline">
              {months.map(([m, n]) => (
                <span key={m} className="bar" style={{ height: `${(n / peak) * 100}%` }}
                  title={`${m}: ${n}`} />
              ))}
            </div>
            <div className="tl-axis"><span>{months[0][0]}</span><span>{months[months.length - 1][0]}</span></div>
          </>
        )}
      </div>

      <div className="card">
        <h2>Analysis (merged)</h2>
        <div className="metrics">
          <Metric k="Avg words / comment" v={metrics.avgWords} />
          <Metric k="Avg sentence length" v={metrics.avgSentence} />
          <Metric k="Vocabulary richness" v={metrics.ttr} />
          <Metric k="Accented-word rate" v={metrics.accentRate} />
          <Metric k="ALL-CAPS words" v={metrics.capsRate} />
          <Metric k="Exclamations / comment" v={metrics.exclam} />
        </div>
        <p className="subtle" style={{ marginTop: 12 }}>
          Computed over {withText.length} comments from all aliases.
        </p>
        <div className="metrics" style={{ marginTop: 14 }}>
          <Metric k="Probable gender" v={<span className="pending">not yet computed</span>} />
          <Metric k="Political tendency" v={<span className="pending">not yet computed</span>} />
          <Metric k="Philosophical leaning" v={<span className="pending">not yet computed</span>} />
          <Metric k="Linguistic region" v={<span className="pending">not yet computed</span>} />
        </div>
      </div>

      <div className="card">
        <h2>Comments ({comments.length})</h2>
        {comments.slice(0, shown).map((c) => (
          <div className="comment" key={c.id}>
            <div className="head">
              <span className="when">{c.posted_at ? fmt(c.posted_at) : 'date unknown'}</span>
              <span className="chip">{c.author_nick}</span>
              <span className="nick" style={{ fontWeight: 400 }} onClick={() => onArticle?.(c.article_id)}>
                {c.headline || '(article)'}
              </span>
              <span className="votes">{c.like_count != null ? `${c.like_count} votes` : ''}</span>
            </div>
            {c.body_text
              ? <div className="text">{c.body_text}</div>
              : <div className="text missing">[body not recoverable]</div>}
          </div>
        ))}
        {comments.length > shown && (
          <button className="btn secondary" style={{ marginTop: 12 }}
            onClick={() => setShown((n) => n + 300)}>
            Show more ({comments.length - shown} remaining)
          </button>
        )}
      </div>
    </>
  )
}

function Metric({ k, v }) {
  return <div className="metric"><div className="v">{v}</div><div className="k">{k}</div></div>
}
