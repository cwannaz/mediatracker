import { useEffect, useMemo, useState } from 'react'

// --------------------------------------------------------------------------- //
// One person's comments: newest first, filtered and sorted in the browser
// --------------------------------------------------------------------------- //
// The whole history is already in memory -- the server sends it in one answer
// -- so every filter here is a pass over an array rather than a round trip.
// That is what makes a regular expression affordable to apply as it is typed.
//
// Shared by the commenter page and the persona page: a person assembled from
// four nicknames is read the same way as a single one, and the only difference
// is that the journal chips then span several titles.
const fmt = (v) => { try { return new Date(v).toLocaleString() } catch { return String(v) } }

const SORTS = {
  date: { label: 'Date', of: (c) => c.posted_at || '' },
  journal: { label: 'Journal', of: (c) => c.journal || '' },
  votes: { label: 'Votes', of: (c) => c.like_count ?? -1 },
}

export default function CommentList({ comments, journals, onArticle, showNick = false }) {
  const [mode, setMode] = useState('text')
  const [raw, setRaw] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [picked, setPicked] = useState([])
  const [undatedOnly, setUndatedOnly] = useState(false)
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

  // Undated comments are a population, not a nuisance: captures whose
  // timestamp we never recovered, which every date range necessarily hides.
  // The only way to read them is to ask for them, so they get their own box.
  const nUndated = useMemo(
    () => comments.reduce((n, c) => n + (c.posted_at ? 0 : 1), 0), [comments])

  const rows = useMemo(() => {
    const hay = (c) => `${c.body_text || ''}\n${c.headline || ''}`
    const out = comments.filter((c) => {
      if (undatedOnly && c.posted_at) return false
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
    // In a date order the undated keep to the end whichever way the rest
    // runs: they belong nowhere on a timeline, and floating them to the top
    // would spend the whole display budget on the rows that say least.
    const of = SORTS[key].of
    const undated = out.filter((c) => !c.posted_at)
    const rest = key === 'date' ? out.filter((c) => c.posted_at) : out
    rest.sort((a, b) => {
      const x = of(a), y = of(b)
      const c = x < y ? -1 : x > y ? 1 : 0
      return desc ? -c : c
    })
    return key === 'date' ? rest.concat(undated) : rest
  }, [comments, picked, undatedOnly, from, to, re, terms, key, desc])

  useEffect(() => { setShown(150) },
    [picked, undatedOnly, from, to, raw, mode, key, desc])

  const filtered = rows.length !== comments.length
  const dirty = filtered || raw || from || to || undatedOnly
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
                 disabled={undatedOnly}
                 onChange={(e) => setFrom(e.target.value)} aria-label="From date" />
          <span className="subtle">–</span>
          <input type="date" className="yr" value={to} min={from || undefined}
                 disabled={undatedOnly}
                 onChange={(e) => setTo(e.target.value)} aria-label="To date" />
        </span>
        {nUndated > 0 && (
          <label className="filter" title="Captures whose timestamp was never recovered">
            <input type="checkbox" checked={undatedOnly}
                   onChange={(e) => setUndatedOnly(e.target.checked)} />
            Undated only
            <span className="count">{nUndated.toLocaleString()}</span>
          </label>
        )}
        <span className="fgroup">
          {Object.entries(SORTS).map(([k, s]) => (
            <button key={k} className={`schip ${key === k ? 'on' : ''}`}
                    aria-label={`Sort by ${s.label}`}
                    onClick={() => { key === k ? setDesc((d) => !d) : setKey(k) }}>
              {s.label}{key === k ? (desc ? ' ↓' : ' ↑') : ''}
            </button>
          ))}
        </span>
        {dirty && (
          <button className="btn secondary"
                  onClick={() => {
                    setRaw(''); setFrom(''); setTo('')
                    setPicked([]); setUndatedOnly(false)
                  }}>
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
            {showNick && c.author_nick && <span className="chip">{c.author_nick}</span>}
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
