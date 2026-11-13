import { useEffect, useMemo, useState } from 'react'
import { languageMetrics } from './textmetrics.js'
import ProfilePanel from './ProfilePanel.jsx'
import PrivateDetails from './PrivateDetails.jsx'
import Notes from './Notes.jsx'
import Accounts from './Accounts.jsx'
import ActivityTimeline, { useCorpusSpan, useCoverage } from './ActivityTimeline.jsx'
import CommentList from './CommentList.jsx'
import SectionRail from './SectionRail.jsx'

const fmtD = (v) => { try { return new Date(v).toLocaleDateString() } catch { return String(v) } }

export default function CommenterView({ nick, send, onBack, onArticle, onPersona }) {
  const [data, setData] = useState(null)
  const [picked, setPicked] = useState([])
  const [busy, setBusy] = useState(false)
  // The whole history, so the filters below act on all of it: asking for a
  // page would make "latest comments" mean "latest of the first thousand".
  const load = () => send('get_commenter', { nick, limit: 50000 })
    .then((r) => { if (r.ok) { setData(r); setPicked([]) } }).catch(() => {})
  useEffect(() => { load() }, [nick, send]) // eslint-disable-line

  const comments = data?.comments || []
  const withText = comments.filter((c) => c.body_text)
  const metrics = useMemo(
    () => languageMetrics(withText.map((c) => c.body_text)), [data]) // eslint-disable-line

  const span = useCorpusSpan(send)
  // Coverage for the titles this person writes in, not the whole corpus:
  // a band drawn from a paper they never touched would say nothing about them.
  const journals = useMemo(
    () => [...new Set(comments.map((c) => c.journal).filter(Boolean))], [comments])
  const coverage = useCoverage(send, journals)

  if (!data) return <><button className="backlink" onClick={onBack}>← Back</button><div className="empty">Loading…</div></>

  const dated = comments.filter((c) => c.posted_at)
  const votes = comments.reduce((s, c) => s + (c.like_count || 0), 0)
  // Read from the values, not from where they sit in the array: the history
  // arrives newest-first so the ends are not where first/last used to be, and
  // the list below re-sorts it anyway.
  const sections = SECTIONS
  const span2 = dated.reduce((acc, c) => ({
    first: !acc.first || c.posted_at < acc.first ? c.posted_at : acc.first,
    last: !acc.last || c.posted_at > acc.last ? c.posted_at : acc.last,
  }), { first: null, last: null })

  return (
    <>
      <button className="backlink" onClick={onBack}>← Back to commenters</button>

      <div className="panel-head"><h1>{nick}</h1>
        <span className="slug">{data.total} comments</span></div>

      <div className="railed">
        <SectionRail sections={sections} />
        <div>
          <section id="sec-identity">
            <Identity nick={nick} data={data} send={send} reload={load}
              picked={picked} setPicked={setPicked} busy={busy} setBusy={setBusy}
              onPersona={onPersona} />
          </section>

          <section id="sec-notes"><Notes nick={nick} send={send} /></section>
          <section id="sec-elsewhere"><Accounts nick={nick} send={send} /></section>

          <section id="sec-metadata" className="card">
            <h2>Metadata</h2>
            <div className="metrics">
              <Metric k="Comments" v={data.total} />
              <Metric k="Articles" v={new Set(comments.map((c) => c.article_id)).size} />
              <Metric k="Journals" v={journals.join(', ') || '—'} />
              <Metric k="First seen" v={dated.length ? fmtD(span2.first) : '—'} />
              <Metric k="Last seen" v={dated.length ? fmtD(span2.last) : '—'} />
              <Metric k="Votes received" v={votes || '—'} />
            </div>
          </section>

          <section id="sec-timeline">
            <ActivityTimeline comments={comments} span={span} coverage={coverage}
              title="Timeline — comments per month" />
          </section>

          <section id="sec-style" className="card">
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
          </section>

          <section id="sec-profile">
            {data.persona
              ? <div className="card">
                  <h2>Profile</h2>
                  <p className="subtle">
                    This nickname belongs to {data.persona.label}; the profile is built on
                    that person's whole body of writing.
                  </p>
                </div>
              : <ProfilePanel nick={nick} send={send} />}
          </section>

          <section id="sec-private"><PrivateDetails nick={nick} send={send} /></section>

          <section id="sec-comments">
            <CommentList comments={comments} journals={journals} onArticle={onArticle} />
          </section>
        </div>
      </div>
    </>
  )
}

// Stable, and declared rather than discovered: a card that is still loading
// has no heading yet, and a rail that grew entries as the page filled would
// move under the cursor.
const SECTIONS = [
  { id: 'sec-identity', label: 'Identity' },
  { id: 'sec-notes', label: 'Notes' },
  { id: 'sec-elsewhere', label: 'Elsewhere' },
  { id: 'sec-metadata', label: 'Metadata' },
  { id: 'sec-timeline', label: 'Timeline' },
  { id: 'sec-style', label: 'Analysis' },
  { id: 'sec-profile', label: 'Profile' },
  { id: 'sec-private', label: 'Private details' },
  { id: 'sec-comments', label: 'Comments' },
]

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
