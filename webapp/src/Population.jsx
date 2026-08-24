import { useEffect, useMemo, useState } from 'react'
import Compass from './Compass.jsx'
import RegionMap from './RegionMap.jsx'

// The profiled commenters taken together — the sociological output of the
// study. Distributions are computed here from the full subject list rather
// than server-side, so a subject can be excluded and everything recomputes
// without another round trip.
//
// Denominators matter as much as shares: "unknown" is reported as a real
// category, because most subjects genuinely leave no evidence of gender or
// region and hiding that would overstate what the corpus can support.

const LEANING_ORDER = ['far-left', 'left', 'centre-left', 'centre',
                       'centre-right', 'right', 'far-right', 'mixed', 'unclear']
const MASTERY_ORDER = ['native-fluent', 'fluent', 'good', 'approximate', 'poor']

const VIEWS = [
  { id: 'overview', label: 'Global analysis' },
  { id: 'subjects', label: 'Subjects' },
]

export default function Population({ send, onNick, onPersona }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  const [view, setView] = useState('overview')
  const [excluded, setExcluded] = useState([])
  const [community, setCommunity] = useState(null)   // null = all of them

  useEffect(() => {
    let live = true
    send('profile_overview').then((r) => {
      if (!live) return
      if (r.ok) setD(r); else setErr(r.error)
    }).catch(() => {})
    return () => { live = false }
  }, [send])

  const all = d?.subjects || []
  const communities = d?.communities || []
  const inScope = useMemo(
    () => (community ? all.filter((s) => s.community === community) : all),
    [all, community])
  const shown = useMemo(
    () => inScope.filter((s) => !excluded.includes(key(s))), [inScope, excluded])

  if (err) return <div className="banner warn">{err}</div>
  if (!d) return <div className="empty">Loading…</div>
  if (!all.length) return <div className="empty">No profiles yet. Run the analysis pass to build them.</div>

  const open = (s) => s.subject_kind === 'persona'
    ? onPersona?.(Number(s.subject_key))
    : onNick?.(s.subject_key)

  return (
    <>
      {communities.length > 1 && (
        <Communities list={communities} value={community} onChange={setCommunity} />
      )}

      <nav className="subtabs inner" role="tablist" aria-label="Population views">
        {VIEWS.map((v) => (
          <button key={v.id} className="subtab" role="tab" aria-selected={view === v.id}
            onClick={() => setView(v.id)}>
            {v.label}
            {v.id === 'subjects' && <span className="count">{shown.length}</span>}
          </button>
        ))}
      </nav>

      <Concentration all={inScope} excluded={excluded} setExcluded={setExcluded} />

      {view === 'overview'
        ? <Overview d={d} subjects={shown} open={open} showCommunity={!community} />
        : <SubjectTable subjects={shown} open={open} showCommunity={!community} />}
    </>
  )
}

// A nickname identifies someone only inside its own comment community, so the
// community has to be part of the key: two platforms can each have a "Marie03"
// and they are two different people.
const key = (s) => `${s.community}:${s.subject_kind}:${s.subject_key}`

const COMMUNITY_LABEL = {
  lematin: 'Le Matin',
  'tx-romandie': '24 heures / Tribune de Genève',
}
const communityName = (c) => COMMUNITY_LABEL[c] || c

// Separate platforms mean separate registrations, so these are separate
// publics: they are never silently pooled. Showing all of them together is
// still useful — it is just always labelled as a sum of distinct populations.
function Communities({ list, value, onChange }) {
  const total = list.reduce((a, c) => a + c.count, 0)
  return (
    <div className="card note">
      <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <span className="subtle">Population:</span>
        <button className={'chip' + (value === null ? ' on' : '')}
          onClick={() => onChange(null)}>All · {total}</button>
        {list.map((c) => (
          <button key={c.community} className={'chip' + (value === c.community ? ' on' : '')}
            onClick={() => onChange(c.community)}>
            {communityName(c.community)} · {c.count}
          </button>
        ))}
      </div>
      <div className="subtle" style={{ marginTop: 6 }}>
        {value === null
          ? 'These are separate commenting platforms. The same nickname on two of'
            + ' them is two people until something proves otherwise, so "All" is a'
            + ' sum of distinct populations rather than one merged public.'
          : 'Narrowed to one platform. Nicknames are only comparable within it.'}
      </div>
    </div>
  )
}

// How much of the corpus one writer accounts for. The archive is built from
// articles someone printed, and people print the threads they took part in, so
// the heaviest subject is heavy by construction rather than by being typical.
function Concentration({ all, excluded, setExcluded }) {
  const total = all.reduce((s, x) => s + (x.n_comments || 0), 0)
  const top = [...all].sort((a, b) => b.n_comments - a.n_comments)[0]
  if (!top || !total) return null
  const share = (top.n_comments / total) * 100
  const next = [...all].sort((a, b) => b.n_comments - a.n_comments)[1]
  if (share < 5) return null
  const isOut = excluded.includes(key(top))

  return (
    <div className="card note">
      <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <strong>{top.label}</strong> alone is {share.toFixed(1)}% of the analysed
          comments ({top.n_comments} of {total.toLocaleString()})
          {next && <> — {(top.n_comments / next.n_comments).toFixed(0)}× the next
            subject, {next.label} ({next.n_comments})</>}.
          <div className="subtle" style={{ marginTop: 4 }}>
            Distributions below count each subject once, so they are unaffected.
            Anything weighted by volume is not.
          </div>
        </div>
        <label className="checkbox" style={{ whiteSpace: 'nowrap' }}>
          <input type="checkbox" checked={isOut}
            onChange={() => setExcluded(isOut ? excluded.filter((k) => k !== key(top))
                                              : [...excluded, key(top)])} />
          Leave out of the figures
        </label>
      </div>
    </div>
  )
}

function Overview({ d, subjects, open }) {
  const n = subjects.length
  const genderOf = (s) => (s.male >= 0.6 ? 'male' : s.female >= 0.6 ? 'female' : 'unknown')
  const drifters = (d.drifters || []).filter((r) =>
    subjects.some((s) => key(s) === key(r)))

  return (
    <>
      <div className="card">
        <h2>Profiled population</h2>
        <div className="metrics">
          <Metric k="Subjects profiled" v={n} />
          <Metric k="Of which people (merged)"
            v={subjects.filter((s) => s.subject_kind === 'persona').length} />
          <Metric k="Comments analysed"
            v={subjects.reduce((a, s) => a + (s.n_comments || 0), 0).toLocaleString()} />
        </div>
        <p className="subtle" style={{ marginTop: 12 }}>
          One subject is one writer: a person where nicknames have been linked,
          otherwise a single nickname. Shares below are of profiled subjects, not
          of comments — a prolific commenter counts once.
        </p>
      </div>

      <Compass subjects={subjects} onOpen={open} />

      <div className="row2">
        <Dist title="Gender" rows={tally(subjects, genderOf)} total={n}
          note="Read only from French grammatical self-reference. Most writers never refer to themselves in a gendered way, so 'unknown' is expected to dominate." />
        <Dist title="Language mastery" rows={tally(subjects, (s) => s.mastery)} total={n}
          order={MASTERY_ORDER}
          note="Command of grammar, syntax and vocabulary. Not typing accents is an input habit and is never counted against mastery." />
      </div>

      <div className="row2">
        <Dist title="Political leaning" rows={tally(subjects, (s) => s.leaning)} total={n}
          order={LEANING_ORDER}
          note="'unclear' means the comments carried no usable position — it is not a centre reading." />
        <RegionMap subjects={subjects} />
      </div>

      {drifters.length > 0 && (
        <div className="card">
          <h2>Changed position over time</h2>
          <p className="subtle">
            Subjects whose recorded stance moves across periods. This is the
            trajectory the study is looking for, and it is rare — check each
            against the comments before treating it as a finding. A period
            marked <em>unclear</em> is a gap in the record, not a change.
          </p>
          <div className="table-wrap"><table>
            <thead><tr><th>Subject</th><th>Trajectory</th></tr></thead>
            <tbody>{drifters.map((r) => (
              <tr key={key(r)} className="rowlink" onClick={() => open(r)}>
                <td><strong>{r.label}</strong>
                  {r.drift && <span className="chip">{r.drift}</span>}</td>
                <td style={{ whiteSpace: 'normal' }}>
                  {(r.periods || []).map((p, i) => (
                    <span key={i}>
                      {i > 0 && <span className="subtle"> → </span>}
                      <span className="chip">{p.from}–{p.to} {p.leaning}</span>
                    </span>
                  ))}
                </td>
              </tr>
            ))}</tbody>
          </table></div>
        </div>
      )}
    </>
  )
}

const COLUMNS = [
  { id: 'label', label: 'Subject', align: 'left' },
  // Only meaningful when several platforms are in view; a nickname is not
  // comparable across them, so the table has to say which one a row is from.
  { id: 'community', label: 'Platform', communityOnly: true },
  { id: 'n_comments', label: 'Comments', num: true },
  { id: 'avg_words', label: 'Avg words / comment', num: true, dp: 1 },
  { id: 'mastery', label: 'Mastery', order: MASTERY_ORDER },
  { id: 'err', label: 'Err / 100w', num: true, dp: 2 },
  { id: 'accents', label: 'Accents' },
  { id: 'register', label: 'Register' },
  { id: 'leaning', label: 'Leaning', order: LEANING_ORDER },
  { id: 'drift', label: 'Drift' },
  { id: 'region', label: 'Region' },
  { id: 'gender', label: 'Gender' },
]

function SubjectTable({ subjects, open, showCommunity }) {
  const [sort, setSort] = useState({ col: 'n_comments', dir: 'desc' })
  const [q, setQ] = useState('')
  const cols = useMemo(
    () => COLUMNS.filter((c) => showCommunity || !c.communityOnly), [showCommunity])

  const rows = useMemo(() => {
    let r = subjects.map((s) => ({
      ...s,
      gender: s.male >= 0.6 ? `male ${Math.round(s.male * 100)}%`
            : s.female >= 0.6 ? `female ${Math.round(s.female * 100)}%` : '—',
      _g: s.male >= 0.6 ? s.male : s.female >= 0.6 ? -s.female : 0,
    }))
    if (q) {
      let re
      try { re = new RegExp(q, 'i') } catch { re = null }
      if (re) r = r.filter((s) => re.test(s.label))
    }
    const col = COLUMNS.find((c) => c.id === sort.col)
    const sign = sort.dir === 'asc' ? 1 : -1
    return r.sort((a, b) => sign * cmp(a, b, col))
  }, [subjects, sort, q])

  const click = (c) => setSort((s) => s.col === c.id
    ? { col: c.id, dir: s.dir === 'asc' ? 'desc' : 'asc' }
    : { col: c.id, dir: c.num ? 'desc' : 'asc' })

  return (
    <>
      <div className="toolbar">
        <input type="text" placeholder="Filter subjects (regex)" value={q}
          onChange={(e) => setQ(e.target.value)} />
        <span className="subtle">{rows.length} subject{rows.length === 1 ? '' : 's'}</span>
      </div>
      <div className="table-wrap"><table className="sortable">
        <thead><tr>
          {cols.map((c) => (
            <th key={c.id} className={(c.num ? 'num ' : '') + 'sortcol'}
              aria-sort={sort.col === c.id ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
              onClick={() => click(c)}>
              {c.label}
              <span className="arrow">{sort.col === c.id ? (sort.dir === 'asc' ? '▲' : '▼') : ''}</span>
            </th>
          ))}
        </tr></thead>
        <tbody>{rows.map((s) => (
          <tr key={key(s)} className="rowlink" onClick={() => open(s)}>
            <td><strong>{s.label}</strong>
              {s.subject_kind === 'persona' && <span className="chip">person</span>}</td>
            {showCommunity && <td className="subtle">{communityName(s.community)}</td>}
            <td className="num">{s.n_comments}</td>
            <td className="num">{s.avg_words != null ? s.avg_words.toFixed(1) : '—'}</td>
            <td>{s.mastery || '—'}</td>
            <td className="num">{s.err != null ? s.err.toFixed(2) : '—'}</td>
            <td>{s.accents || '—'}</td>
            <td>{s.register || '—'}</td>
            <td>{s.leaning || '—'}</td>
            <td>{s.drift && s.drift !== 'none' ? s.drift : <span className="subtle">—</span>}</td>
            <td>{s.region || '—'}</td>
            <td>{s.gender === '—' ? <span className="subtle">—</span> : s.gender}</td>
          </tr>
        ))}</tbody>
      </table></div>
    </>
  )
}

// Sorting has to cope with three kinds of column: numbers, ordered categories
// (mastery runs best-to-worst, not alphabetically) and plain text. Missing
// values always sink to the bottom rather than sorting as "".
function cmp(a, b, col) {
  if (!col) return 0
  if (col.id === 'gender') return (a._g || 0) - (b._g || 0)
  const va = a[col.id], vb = b[col.id]
  const na = va == null || va === '', nb = vb == null || vb === ''
  if (na && nb) return 0
  if (na) return 1
  if (nb) return -1
  if (col.num) return va - vb
  if (col.order) {
    const ia = col.order.indexOf(va), ib = col.order.indexOf(vb)
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
  }
  return String(va).localeCompare(String(vb))
}

function tally(subjects, pick) {
  const m = new Map()
  for (const s of subjects) {
    const k = pick(s) || 'unknown'
    m.set(k, (m.get(k) || 0) + 1)
  }
  return [...m.entries()].map(([k, count]) => ({ k, count }))
    .sort((a, b) => b.count - a.count)
}

function Dist({ title, rows, total, note, order }) {
  let data = rows || []
  if (order) {
    data = [...data].sort((a, b) => {
      const ia = order.indexOf(a.k), ib = order.indexOf(b.k)
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
    })
  }
  const peak = Math.max(1, ...data.map((r) => r.count))
  return (
    <div className="card">
      <h2>{title}</h2>
      {data.length === 0 ? <div className="empty">No data.</div> : data.map((r) => (
        <div className="distrow" key={r.k}>
          <span className="lab">{r.k}</span>
          <span className="track"><span className="fill" style={{ width: `${(r.count / peak) * 100}%` }} /></span>
          <span className="n">{r.count}</span>
          <span className="pct">{total ? `${Math.round((r.count / total) * 100)}%` : ''}</span>
        </div>
      ))}
      {note && <p className="subtle" style={{ marginTop: 10 }}>{note}</p>}
    </div>
  )
}

function Metric({ k, v }) {
  return <div className="metric"><div className="v">{v}</div><div className="k">{k}</div></div>
}
