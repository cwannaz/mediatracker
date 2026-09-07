import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ImageGrid from './ImageGrid.jsx'

// Search across everything the tracker holds.
//
// This is the spine the rest of the application now hangs from. Commenters
// were the first question asked of this corpus, not the only one, and a
// nickname is a poor entry point for "what did these papers say about Geneva
// in 2015". So the index treats an article, a photograph and a comment as
// three kinds of the same thing — a document with a date, a journal and some
// text — and this view lets you move between them without changing tool.
//
// Two things are deliberately visible rather than hidden. The mode switch,
// because a stemmed search and a regular expression answer different
// questions and pretending otherwise makes both worse. And the daemon's own
// note about HOW a search was answered — prefiltered, scanned, timed out —
// because a truncated result and a complete one look identical otherwise, and
// the difference is the whole basis for trusting a count.

const KINDS = [
  { id: 'article', label: 'Articles' },
  { id: 'image', label: 'Pictures' },
  { id: 'comment', label: 'Comments' },
]
const JOURNALS = [
  { id: 'lematin', label: 'Le Matin' },
  { id: '24heures', label: '24 heures' },
  { id: 'tdg', label: 'Tribune de Genève' },
]
const PAGE = 60
const ENT_KINDS = { person: 'Person', organization: 'Organisation',
                    place: 'Place', event: 'Event', topic: 'Topic' }

// ts_headline marks hits with << >>; render them rather than print them.
function Highlight({ text }) {
  if (!text) return null
  const parts = String(text).split(/(<<[^>]*?>>)/g)
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith('<<') && p.endsWith('>>')
          ? <mark key={i}>{p.slice(2, -2)}</mark>
          : <span key={i}>{p}</span>)}
    </>
  )
}

export default function Search({ connected, send, navigate }) {
  const [raw, setRaw] = useState('')
  const [q, setQ] = useState('')
  const [mode, setMode] = useState('text')
  const [kinds, setKinds] = useState([])
  const [journals, setJournals] = useState([])
  const [yearFrom, setYearFrom] = useState('')
  const [yearTo, setYearTo] = useState('')
  const [res, setRes] = useState(null)
  const [rows, setRows] = useState([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [status, setStatus] = useState(null)
  const [ents, setEnts] = useState([])
  const [entity, setEntity] = useState(null)   // {id, kind, name} or null
  const seq = useRef(0)

  // Debounce typing, but not in regex mode: a half-typed expression is
  // usually invalid, and firing a scan at every keystroke is how you make a
  // search box that fights the person using it.
  useEffect(() => {
    if (mode === 'regex') return undefined
    const t = setTimeout(() => setQ(raw), 280)
    return () => clearTimeout(t)
  }, [raw, mode])

  useEffect(() => { if (connected) send('search_status').then(setStatus).catch(() => {}) },
    [connected, send])

  const args = useMemo(() => ({
    q, mode, kinds, journals,
    year_from: yearFrom ? Number(yearFrom) : null,
    year_to: yearTo ? Number(yearTo) : null,
    entity_id: entity?.id ?? null,
  }), [q, mode, kinds, journals, yearFrom, yearTo, entity])

  // Who and what the words name, offered beside the results rather than
  // instead of them: the corpus is read progressively, so an entity rail that
  // replaced the text search would go blank for anything not yet extracted.
  useEffect(() => {
    if (!connected || !q || mode === 'regex') { setEnts([]); return undefined }
    let live = true
    send('entity_lookup', { q, limit: 12 })
      .then((r) => { if (live && r.ok) setEnts(r.entities || []) })
      .catch(() => {})
    return () => { live = false }
  }, [q, mode, connected, send])

  const run = useCallback((offset) => {
    if (!connected) return
    const mine = ++seq.current
    setBusy(true)
    setErr(null)
    send('search', { ...args, limit: PAGE, offset })
      .then((r) => {
        if (mine !== seq.current) return          // a later search won
        if (!r.ok) { setErr(r.error || 'search failed'); return }
        setRes(r)
        setRows((prev) => (offset ? [...prev, ...r.rows] : r.rows))
      })
      .catch((e) => { if (mine === seq.current) setErr(String(e)) })
      .finally(() => { if (mine === seq.current) setBusy(false) })
  }, [args, connected, send])

  // Clear before re-running, not just on arrival. Leaving the previous rows
  // on screen under the new filter chips shows results that do not answer the
  // question now being asked, and the picture grid would then try to render
  // articles as photographs.
  useEffect(() => { setRows([]); setRes(null); run(0) }, [run])

  const submit = (e) => { e.preventDefault(); setQ(raw) }
  const toggle = (list, set, id) =>
    set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id])

  // Both conditions matter. The filter says what the reader asked for; the
  // rows say what actually arrived. Trusting only the filter renders whatever
  // is in state as pictures, which is how article ids ended up being fetched
  // from the thumbnail route.
  const gridMode = kinds.length === 1 && kinds[0] === 'image' &&
                   rows.length > 0 && rows.every((r) => r.kind === 'image')
  const facets = res?.facets || {}
  const more = rows.length > 0 && rows.length % PAGE === 0

  return (
    <main className="searchpage">
      <form className="searchbar" onSubmit={submit}>
        <input
          className="searchbox"
          value={raw}
          onChange={(e) => setRaw(e.target.value)}
          placeholder={mode === 'regex'
            ? 'POSIX regular expression, e.g. (?:grand )?remplacement'
            : 'Search articles, pictures and comments…'}
          aria-label="Search"
          spellCheck={false}
        />
        <div className="modeswitch" role="group" aria-label="Search mode">
          {['text', 'regex'].map((m) => (
            <button key={m} type="button"
                    className={mode === m ? 'on' : ''}
                    onClick={() => { setMode(m); setQ(raw) }}>
              {m === 'text' ? 'Text' : 'Regex'}
            </button>
          ))}
        </div>
        {mode === 'regex' && (
          <button className="btn" type="submit">Run</button>
        )}
      </form>

      <div className="sfilters">
        <span className="fgroup">
          {KINDS.map((k) => (
            <button key={k.id}
                    className={`schip ${kinds.includes(k.id) ? 'on' : ''}`}
                    onClick={() => toggle(kinds, setKinds, k.id)}>
              {k.label}
              {facets[k.id] != null && <span className="schipn">{facets[k.id].toLocaleString()}</span>}
            </button>
          ))}
        </span>
        <span className="fgroup">
          {JOURNALS.map((j) => (
            <button key={j.id}
                    className={`schip ${journals.includes(j.id) ? 'on' : ''}`}
                    onClick={() => toggle(journals, setJournals, j.id)}>
              {j.label}
            </button>
          ))}
        </span>
        <span className="fgroup">
          <input className="yr" value={yearFrom} onChange={(e) => setYearFrom(e.target.value)}
                 placeholder="from" inputMode="numeric" aria-label="Year from" />
          <span className="subtle">–</span>
          <input className="yr" value={yearTo} onChange={(e) => setYearTo(e.target.value)}
                 placeholder="to" inputMode="numeric" aria-label="Year to" />
        </span>
      </div>

      {entity && (
        <div className="entbar">
          <span className="entpill">
            <span className="entkind">{ENT_KINDS[entity.kind] || entity.kind}</span>
            {entity.name}
            <button className="entx" onClick={() => setEntity(null)}
                    aria-label="Clear entity filter">✕</button>
          </span>
          <span className="subtle">
            showing only documents that mention this entity
          </span>
        </div>
      )}

      {!entity && ents.length > 0 && (
        <div className="entrail">
          <span className="subtle">Mentions:</span>
          {ents.map((e) => (
            <button key={e.id} className="entsug" onClick={() => setEntity(e)}
                    title={`${ENT_KINDS[e.kind] || e.kind} · ${e.mentions} mentions`}>
              {e.name}
              <span className="entkind">{ENT_KINDS[e.kind] || e.kind}</span>
            </button>
          ))}
        </div>
      )}

      <div className="resline subtle">
        {busy && 'searching… '}
        {res && !busy && (
          <>
            {res.total.toLocaleString()}{res.truncated ? '+' : ''} match
            {res.total === 1 ? '' : 'es'} in {res.took_ms} ms
            {res.note && <span className="snote"> · {res.note}</span>}
            {res.truncated && (
              <span className="snote"> · counts capped; narrow the search to
                get an exact figure</span>
            )}
          </>
        )}
        {status?.entities != null && status.entities.pct < 99.5 && (
          <span className="snote"> · entities read from {status.entities.pct}% of
            articles so far ({status.entities.entities.toLocaleString()} found)</span>
        )}
        {status?.kinds && !q && !busy && (
          <span className="snote"> · index holds{' '}
            {KINDS.map((k) => `${(status.kinds[k.id]?.docs || 0).toLocaleString()} ${k.label.toLowerCase()}`)
              .join(', ')}
          </span>
        )}
      </div>

      {err && <div className="card serror">{err}</div>}

      {!err && rows.length === 0 && !busy && (
        <div className="card">
          <p className="subtle">
            {q
              ? 'Nothing matched. In text mode the words are stemmed and accents ignored, so "geneve" finds "Genève"; regex mode matches literally.'
              : 'Type to search, or pick a kind to browse the newest of it.'}
          </p>
        </div>
      )}

      {gridMode
        ? <ImageGrid rows={rows} more={more} onNeedMore={() => run(rows.length)} />
        : (
          <div className="results">
            {rows.map((r) => (
              <Result key={`${r.kind}:${r.ref}`} row={r} navigate={navigate} />
            ))}
            {more && (
              <div className="more-row">
                <button className="linkish" onClick={() => run(rows.length)}>
                  load more
                </button>
              </div>
            )}
          </div>
        )}
    </main>
  )
}

function Result({ row, navigate }) {
  const x = row.extra || {}
  if (row.kind === 'image') {
    return (
      <div className="result img">
        <img className="rthumb" src={`/thumb/t/${row.ref}`} alt="" loading="lazy" />
        <div>
          <div className="rtitle"><Highlight text={row.title} /></div>
          <div className="rmeta subtle">
            {['Picture', row.journal, row.when, x.credit].filter(Boolean).join(' · ')}
          </div>
          {x.headline && <div className="rsnip subtle">Ran under: {x.headline}</div>}
        </div>
      </div>
    )
  }
  if (row.kind === 'comment') {
    return (
      <div className="result">
        <div className="rmeta subtle">
          Comment · <button className="linkish"
            onClick={() => navigate?.(['commenters', x.nick])}>{x.nick}</button>
          {row.journal ? ` · ${row.journal}` : ''}{row.when ? ` · ${row.when}` : ''}
          {x.is_reply ? ' · reply' : ''}
        </div>
        <div className="rsnip"><Highlight text={row.snippet} /></div>
        {x.headline && <div className="rmeta subtle">under “{x.headline}”</div>}
      </div>
    )
  }
  return (
    <div className="result">
      <div className="rtitle">
        {x.url
          ? <a href={x.url} target="_blank" rel="noreferrer"><Highlight text={row.title} /></a>
          : <Highlight text={row.title} />}
      </div>
      <div className="rmeta subtle">
        {[row.journal, row.when, x.section, x.author,
          x.comments ? `${x.comments} comments` : null,
          x.origin].filter(Boolean).join(' · ')}
      </div>
      <div className="rsnip"><Highlight text={row.snippet} /></div>
    </div>
  )
}
