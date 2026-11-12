import { useEffect, useMemo, useState } from 'react'
import { languageMetrics } from './textmetrics.js'
import ProfilePanel from './ProfilePanel.jsx'
import PrivateDetails from './PrivateDetails.jsx'
import Notes from './Notes.jsx'
import Accounts from './Accounts.jsx'
import ActivityTimeline, { useCorpusSpan, useCoverage } from './ActivityTimeline.jsx'

const fmt = (v) => { try { return new Date(v).toLocaleString() } catch { return String(v) } }
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
  const span2 = dated.reduce((acc, c) => ({
    first: !acc.first || c.posted_at < acc.first ? c.posted_at : acc.first,
    last: !acc.last || c.posted_at > acc.last ? c.posted_at : acc.last,
  }), { first: null, last: null })

  return (
    <>
      <button className="backlink" onClick={onBack}>← Back to commenters</button>

      <div className="panel-head"><h1>{nick}</h1>
        <span className="slug">{data.total} comments</span></div>

      <Identity nick={nick} data={data} send={send} reload={load}
        picked={picked} setPicked={setPicked} busy={busy} setBusy={setBusy}
        onPersona={onPersona} />

      <Notes nick={nick} send={send} />
      <Accounts nick={nick} send={send} />

      <div className="card">
        <h2>Metadata</h2>
        <div className="metrics">
          <Metric k="Comments" v={data.total} />
          <Metric k="Articles" v={new Set(comments.map((c) => c.article_id)).size} />
          <Metric k="Journals" v={journals.join(', ') || '—'} />
          <Metric k="First seen" v={dated.length ? fmtD(span2.first) : '—'} />
          <Metric k="Last seen" v={dated.length ? fmtD(span2.last) : '—'} />
          <Metric k="Votes received" v={votes || '—'} />
        </div>
      </div>

      <ActivityTimeline comments={comments} span={span} coverage={coverage}
        title="Timeline — comments per month" />

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
      <PrivateDetails nick={nick} send={send} />

      <CommentList comments={comments} journals={journals} onArticle={onArticle} />
    </>
  )
}

// --------------------------------------------------------------------------- //
// The comment list: newest first, filtered and sorted in the browser
// --------------------------------------------------------------------------- //
// The whole history is already in memory -- the server sends it in one answer
// -- so every filter here is a pass over an array rather than a round trip.
// That is what makes a regular expression affordable to apply as it is typed.
const SORTS = {
  date: { label: 'Date', of: (c) => c.posted_at || '' },
  journal: { label: 'Journal', of: (c) => c.journal || '' },
  votes: { label: 'Votes', of: (c) => c.like_count ?? -1 },
}

function CommentList({ comments, journals, onArticle }) {
  const [mode, setMode] = useState('text')
  const [raw, setRaw] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [picked, setPicked] = useState([])
  const [key, setKey] = useState('date')
  const [desc, setDesc] = useState(true)
  const [shown, setShown] = useState(150)

  // A bad regular expression is a half-typed one, not an error to shout about:
  // the filter stands still and says so until it parses.
  const { re, badRe } = useMemo(() => {
    if (mode !== 'regex' || !raw) return { re: null, badRe: null }
    try { return { re: new RegExp(raw, 'i'), badRe: null } }
    catch (e) { return { re: null, badRe: e.message } }
  }, [mode, raw])

  const terms = useMemo(
    () => (mode === 'text' ? raw.toLowerCase().split(/\s+/).filter(Boolean) : []),
    [mode, raw])

  const rows = useMemo(() => {
    const hay = (c) => `${c.body_text || ''}\n${c.headline || ''}`
    const out = comments.filter((c) => {
      if (picked.length && !picked.includes(c.journal)) return false
      if (from && (!c.posted_at || c.posted_at.slice(0, 10) < from)) return false
      if (to && (!c.posted_at || c.posted_at.slice(0, 10) > to)) return false
      if (re) return re.test(hay(c))
      if (terms.length) {
        const h = hay(c).toLowerCase()
        return terms.every((t) => h.includes(t))
      }
      return true
    })
    // Undated comments keep to the end whichever way the rest runs: their
    // timestamp was never recovered, so they belong nowhere in a date order.
    const of = SORTS[key].of
    const undated = out.filter((c) => !c.posted_at)
    const rest = key === 'date' ? out.filter((c) => c.posted_at) : out
    rest.sort((a, b) => {
      const x = of(a), y = of(b)
      const c = x < y ? -1 : x > y ? 1 : 0
      return desc ? -c : c
    })
    return key === 'date' ? rest.concat(undated) : rest
  }, [comments, picked, from, to, re, terms, key, desc])

  useEffect(() => { setShown(150) }, [picked, from, to, raw, mode, key, desc])

  const filtered = rows.length !== comments.length
  return (
    <div className="card">
      <h2>Comments</h2>

      <div className="toolbar">
        <input
          className="searchbox"
          value={raw}
          onChange={(e) => setRaw(e.target.value)}
          placeholder={mode === 'regex'
            ? 'Regular expression, e.g. (?:im)?migration'
            : 'Words that must all appear, in the comment or its headline'}
          aria-label="Filter comments"
          spellCheck={false}
        />
        <div className="modeswitch" role="group" aria-label="Filter mode">
          {['text', 'regex'].map((m) => (
            <button key={m} type="button" className={mode === m ? 'on' : ''}
                    onClick={() => setMode(m)}>
              {m === 'text' ? 'Words' : 'Regex'}
            </button>
          ))}
        </div>
      </div>

      <div className="sfilters">
        {journals.length > 1 && (
          <span className="fgroup">
            {journals.map((j) => (
              <button key={j} className={`schip ${picked.includes(j) ? 'on' : ''}`}
                      onClick={() => setPicked((p) =>
                        p.includes(j) ? p.filter((x) => x !== j) : [...p, j])}>
                {j}
                <span className="schipn">
                  {comments.filter((c) => c.journal === j).length.toLocaleString()}
                </span>
              </button>
            ))}
          </span>
        )}
        <span className="fgroup">
          <input type="date" className="yr" value={from} max={to || undefined}
                 onChange={(e) => setFrom(e.target.value)} aria-label="From date" />
          <span className="subtle">–</span>
          <input type="date" className="yr" value={to} min={from || undefined}
                 onChange={(e) => setTo(e.target.value)} aria-label="To date" />
        </span>
        <span className="fgroup">
          {Object.entries(SORTS).map(([k, s]) => (
            <button key={k} className={`schip ${key === k ? 'on' : ''}`}
                    aria-label={`Sort by ${s.label}`}
                    onClick={() => { key === k ? setDesc((d) => !d) : setKey(k) }}>
              {s.label}{key === k ? (desc ? ' ↓' : ' ↑') : ''}
            </button>
          ))}
        </span>
        {(filtered || raw || from || to) && (
          <button className="btn secondary"
                  onClick={() => { setRaw(''); setFrom(''); setTo(''); setPicked([]) }}>
            Clear
          </button>
        )}
      </div>

      {badRe && <p className="subtle">Incomplete expression: {badRe}</p>}
      <p className="subtle" style={{ marginBottom: 12 }}>
        {filtered
          ? `${rows.length.toLocaleString()} of ${comments.length.toLocaleString()} comments`
          : `${comments.length.toLocaleString()} comments`}
      </p>

      {rows.length === 0 ? <div className="empty">None.</div> : rows.slice(0, shown).map((c) => (
        <div className="comment" key={c.id}>
          <div className="head">
            <span className="when">{c.posted_at ? fmt(c.posted_at) : 'date unknown'}</span>
            <span className="nick" style={{ fontWeight: 400 }}
              onClick={() => onArticle?.(c.article_id)}>
              {c.headline || '(article)'}
            </span>
            {c.journal && <span className="subtle">{c.journal}</span>}
            {c.canonical_url && (
              <a className="subtle" href={c.canonical_url}
                 target="_blank" rel="noreferrer noopener"
                 title={c.canonical_url}>source ↗</a>
            )}
            <span className="votes">{c.like_count != null ? `${c.like_count} votes` : ''}</span>
          </div>
          {c.body_text
            ? <div className="text">{c.body_text}</div>
            : <div className="text missing">[body not recoverable from this capture]</div>}
        </div>
      ))}
      {rows.length > shown && (
        <button className="btn secondary" style={{ marginTop: 12 }}
          onClick={() => setShown((n) => n + 300)}>
          Show more ({(rows.length - shown).toLocaleString()} remaining)
        </button>
      )}
    </div>
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
