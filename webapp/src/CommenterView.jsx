import { useEffect, useMemo, useState } from 'react'
import { languageMetrics } from './textmetrics.js'
import ProfilePanel from './ProfilePanel.jsx'

const fmt = (v) => { try { return new Date(v).toLocaleString() } catch { return String(v) } }
const fmtD = (v) => { try { return new Date(v).toLocaleDateString() } catch { return String(v) } }

export default function CommenterView({ nick, send, onBack, onArticle, onPersona }) {
  const [data, setData] = useState(null)
  const [picked, setPicked] = useState([])
  const [busy, setBusy] = useState(false)
  const [shown, setShown] = useState(150)

  const load = () => send('get_commenter', { nick, limit: 2000 })
    .then((r) => { if (r.ok) { setData(r); setPicked([]) } }).catch(() => {})
  useEffect(() => { load() }, [nick, send]) // eslint-disable-line

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

      <Identity nick={nick} data={data} send={send} reload={load}
        picked={picked} setPicked={setPicked} busy={busy} setBusy={setBusy}
        onPersona={onPersona} />

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
      </div>

      {data.persona
        ? <div className="card">
            <h2>Profile</h2>
            <p className="subtle">
              This nickname belongs to {data.persona.label}; the profile is built on
              that person's whole body of writing.
            </p>
          </div>
        : <ProfilePanel nick={nick} send={send} />}

      <div className="card">
        <h2>Comments</h2>
        {comments.length === 0 ? <div className="empty">None.</div> : comments.slice(0, shown).map((c) => (
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

// Identity: is this nickname already known to be one person's alias, and which
// other nicknames look like the same handle re-spelled?
function Identity({ nick, data, send, reload, picked, setPicked, busy, setBusy, onPersona }) {
  const persona = data.persona
  const suggestions = data.suggestions || []
  const [label, setLabel] = useState(nick)

  const toggle = (n) => setPicked((p) => p.includes(n) ? p.filter((x) => x !== n) : [...p, n])

  const link = async () => {
    setBusy(true)
    try {
      await send('link_nicks', {
        persona_id: persona?.id,
        label: persona ? undefined : label,
        nicks: persona ? picked : [nick, ...picked],
        confidence: 'confirmed',
        evidence: 'linked by hand in the GUI',
      })
      await reload()
    } finally { setBusy(false) }
  }

  return (
    <div className="card">
      <h2>Identity</h2>
      {persona ? (
        <p>
          Part of <button className="backlink" style={{ padding: 0 }}
            onClick={() => onPersona?.(persona.id)}>{persona.label}</button>
          {' '}— {persona.aliases.length} aliases ({persona.confidence}).
          Analysis for the whole person is on that page.
        </p>
      ) : (
        <p className="subtle">Not linked to a person yet.</p>
      )}

      {suggestions.length > 0 && (
        <>
          <p className="subtle" style={{ marginTop: 8 }}>
            Same handle, spelled differently — tick the ones that are the same person:
          </p>
          <div className="row" style={{ flexWrap: 'wrap', gap: 14, margin: '8px 0' }}>
            {suggestions.map((s) => (
              <label key={s.nick} className="checkbox">
                <input type="checkbox" checked={picked.includes(s.nick)}
                  onChange={() => toggle(s.nick)} />
                {s.nick} <span className="subtle">({s.comments})</span>
              </label>
            ))}
          </div>
        </>
      )}

      <div className="actions">
        {!persona && picked.length > 0 && (
          <input type="text" value={label} onChange={(e) => setLabel(e.target.value)}
            style={{ maxWidth: 240 }} placeholder="Name for this person" />
        )}
        <button className="btn" disabled={busy || (!persona && picked.length === 0) || (persona && picked.length === 0)}
          onClick={link}>
          {persona ? 'Add selected to this person' : 'Link selected as one person'}
        </button>
      </div>
      <p className="subtle" style={{ marginTop: 6 }}>
        Suggestions are a spelling heuristic only — confirm before linking.
      </p>
    </div>
  )
}
