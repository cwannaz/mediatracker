import { useEffect, useMemo, useState } from 'react'
import { languageMetrics } from './textmetrics.js'

const fmt = (v) => { try { return new Date(v).toLocaleString() } catch { return String(v) } }
const fmtD = (v) => { try { return new Date(v).toLocaleDateString() } catch { return String(v) } }

export default function CommenterView({ nick, send, onBack, onArticle }) {
  const [data, setData] = useState(null)

  useEffect(() => {
    let alive = true
    send('get_commenter', { nick, limit: 2000 })
      .then((r) => { if (alive && r.ok) setData(r) }).catch(() => {})
    return () => { alive = false }
  }, [nick, send])

  const comments = data?.comments || []
  const withText = comments.filter((c) => c.body_text)
  const metrics = useMemo(
    () => languageMetrics(withText.map((c) => c.body_text)), [data]) // eslint-disable-line

  const months = useMemo(() => {
    const m = new Map()
    for (const c of comments) {
      if (!c.posted_at) continue
      const k = String(c.posted_at).slice(0, 7)
      m.set(k, (m.get(k) || 0) + 1)
    }
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [data]) // eslint-disable-line

  if (!data) return <><button className="backlink" onClick={onBack}>← Back</button><div className="empty">Loading…</div></>

  const peak = Math.max(1, ...months.map(([, n]) => n))
  const dated = comments.filter((c) => c.posted_at)
  const journals = [...new Set(comments.map((c) => c.journal))]
  const votes = comments.reduce((s, c) => s + (c.like_count || 0), 0)

  return (
    <>
      <button className="backlink" onClick={onBack}>← Back to commenters</button>

      <div className="panel-head"><h1>{nick}</h1>
        <span className="slug">{data.total} comments</span></div>

      <div className="card">
        <h2>Metadata</h2>
        <div className="metrics">
          <Metric k="Comments" v={data.total} />
          <Metric k="Articles" v={new Set(comments.map((c) => c.article_id)).size} />
          <Metric k="Journals" v={journals.join(', ') || '—'} />
          <Metric k="First seen" v={dated.length ? fmtD(dated[0].posted_at) : '—'} />
          <Metric k="Last seen" v={dated.length ? fmtD(dated[dated.length - 1].posted_at) : '—'} />
          <Metric k="Votes received" v={votes || '—'} />
        </div>
      </div>

      <div className="card">
        <h2>Timeline — comments per month</h2>
        {months.length === 0 ? <div className="empty">No dated comments.</div> : (
          <>
            <div className="timeline">
              {months.map(([m, n]) => (
                <span key={m} className="bar" style={{ height: `${(n / peak) * 100}%` }}
                  title={`${m}: ${n} comment${n > 1 ? 's' : ''}`} />
              ))}
            </div>
            <div className="tl-axis"><span>{months[0][0]}</span><span>{months[months.length - 1][0]}</span></div>
          </>
        )}
      </div>

      <div className="card">
        <h2>Analysis</h2>
        <div className="metrics">
          <Metric k="Avg words / comment" v={metrics.avgWords} />
          <Metric k="Avg sentence length" v={metrics.avgSentence} />
          <Metric k="Vocabulary richness" v={metrics.ttr} />
          <Metric k="Accented-word rate" v={metrics.accentRate} />
          <Metric k="ALL-CAPS words" v={metrics.capsRate} />
          <Metric k="Exclamations / comment" v={metrics.exclam} />
        </div>
        <p className="subtle" style={{ marginTop: 12 }}>
          Computed from {withText.length} comment{withText.length === 1 ? '' : 's'} with recoverable text.
          These are descriptive style measures, not a mastery score.
        </p>
        <div style={{ marginTop: 14 }}>
          <div className="metrics">
            <Metric k="Probable gender" v={<span className="pending">not yet computed</span>} />
            <Metric k="Political tendency" v={<span className="pending">not yet computed</span>} />
            <Metric k="Philosophical leaning" v={<span className="pending">not yet computed</span>} />
            <Metric k="Linguistic region" v={<span className="pending">not yet computed</span>} />
          </div>
          <p className="subtle" style={{ marginTop: 10 }}>
            Inferred attributes (with probabilities and drift over time) come from the profiling pass,
            which is not built yet.
          </p>
        </div>
      </div>

      <div className="card">
        <h2>Comments</h2>
        {comments.length === 0 ? <div className="empty">None.</div> : comments.map((c) => (
          <div className="comment" key={c.id}>
            <div className="head">
              <span className="when">{c.posted_at ? fmt(c.posted_at) : 'date unknown'}</span>
              <span className="nick" style={{ fontWeight: 400 }}
                onClick={() => onArticle?.(c.article_id)}>
                {c.headline || '(article)'}
              </span>
              <span className="votes">{c.like_count != null ? `${c.like_count} votes` : ''}</span>
            </div>
            {c.body_text
              ? <div className="text">{c.body_text}</div>
              : <div className="text missing">[body not recoverable from this capture]</div>}
          </div>
        ))}
      </div>
    </>
  )
}

function Metric({ k, v }) {
  return <div className="metric"><div className="v">{v}</div><div className="k">{k}</div></div>
}
