import { useEffect, useMemo, useState } from 'react'

// A commenter's months, drawn against the WHOLE corpus by default.
//
// The old plot listed only the months a person actually wrote in, packed side
// by side. That made everyone look equally busy for their whole span and hid
// the two things worth seeing: WHEN in the paper's history they appear, and
// how long they were silent. A four-year gap rendered as two adjacent bars.
//
// So the axis is the corpus, every month between its first comment and its
// last, and an absent month is drawn as an absence. The cost is that a brief
// commenter becomes a thin cluster in a wide field, which is exactly the
// information that was missing -- and the zoom checkbox gives the old view
// back when the shape within a span is what matters.

// Distinguishable at 2px wide, and legible on both themes. Order is fixed so
// a nickname keeps its colour between the stacked and the split view.
const NICK_COLOURS = [
  '#5b8cff', '#FFC000', '#2ea043', '#d1495b',
  '#a06cd5', '#00b3a4', '#e8833a', '#8c9bab',
]
export const colourFor = (i) => NICK_COLOURS[i % NICK_COLOURS.length]

const monthOf = (ts) => String(ts).slice(0, 7)

// Every month from a to b inclusive, so silence occupies its real width.
export function monthRange(a, b) {
  if (!a || !b || a > b) return []
  const out = []
  let [y, m] = a.split('-').map(Number)
  const [ey, em] = b.split('-').map(Number)
  while (y < ey || (y === ey && m <= em)) {
    out.push(`${y}-${String(m).padStart(2, '0')}`)
    if (++m > 12) { m = 1; y++ }
  }
  return out
}

// Roughly `want` evenly spaced labels, snapped to the buckets that exist.
function axisTicks(months, want = 6) {
  if (months.length <= 2) return months.map((m, i) => ({ m, i }))
  const step = (months.length - 1) / (Math.min(want, months.length) - 1)
  const seen = new Set()
  const out = []
  for (let k = 0; k < want; k++) {
    const i = Math.round(k * step)
    if (i < months.length && !seen.has(i)) { seen.add(i); out.push({ m: months[i], i }) }
  }
  return out
}

export default function ActivityTimeline({ comments, span, title, coverage, defaultSplit = false }) {
  const [zoom, setZoom] = useState(false)
  const [split, setSplit] = useState(defaultSplit)
  const [bands, setBands] = useState(true)

  // Per nickname, per month. Built once; both layouts read the same counts so
  // the two views can never disagree about a number.
  const { nicks, byNick, own } = useMemo(() => {
    const per = new Map()
    let lo = null, hi = null
    for (const c of comments) {
      if (!c.posted_at) continue
      const k = monthOf(c.posted_at)
      if (lo === null || k < lo) lo = k
      if (hi === null || k > hi) hi = k
      const nick = c.author_nick || '—'
      if (!per.has(nick)) per.set(nick, new Map())
      const mm = per.get(nick)
      mm.set(k, (mm.get(k) || 0) + 1)
    }
    // Busiest first, so the legend order matches what the eye picks out.
    const order = [...per.entries()]
      .sort((a, b) => sum(b[1]) - sum(a[1])).map(([n]) => n)
    return { nicks: order, byNick: per, own: [lo, hi] }
  }, [comments])

  const cov = useMemo(() => {
    const m = new Map()
    for (const r of coverage || []) m.set(r.month, r)
    return m
  }, [coverage])

  const [corpusLo, corpusHi] = span || []
  const from = zoom || !corpusLo ? own[0] : monthOf(corpusLo)
  const to = zoom || !corpusHi ? own[1] : monthOf(corpusHi)
  // A commenter can fall outside a stale corpus span; never clip their data.
  const months = useMemo(
    () => monthRange(min(from, own[0]), max(to, own[1])), [from, to, own[0], own[1]])

  const multi = nicks.length > 1
  const showSplit = multi && split

  const totalAt = (m) => nicks.reduce((s, n) => s + (byNick.get(n).get(m) || 0), 0)
  const peak = Math.max(1, ...months.map(totalAt))
  // Split lanes share one scale: a lane twice as tall means twice as many
  // comments, which is the comparison the layout exists to make.
  const lanePeak = Math.max(1, ...months.flatMap(
    (m) => nicks.map((n) => byNick.get(n).get(m) || 0)))

  // One band per run of equally-shaded months, so a long uncovered stretch is
  // a single wash rather than 40 abutting rectangles with seams between them.
  const runs = useMemo(() => {
    if (!bands || cov.size === 0) return []
    const out = []
    months.forEach((m, i) => {
      const c = cov.get(m)
      const shade = c === undefined ? UNKNOWN
        : c.live ? 0
          : c.coverage === null ? UNKNOWN
            : 1 - c.coverage
      if (shade <= 0.02) return
      const last = out[out.length - 1]
      if (last && last.to === i - 1 && Math.abs(last.shade - shade) < 0.02) {
        last.to = i
      } else {
        out.push({ from: i, to: i, shade, month: m })
      }
    })
    return out
  }, [months, cov, bands])

  const bandEls = runs.map((r) => (
    <span key={r.from} className="tl-band"
      style={{ left: `${(r.from / months.length) * 100}%`,
               width: `${((r.to - r.from + 1) / months.length) * 100}%`,
               opacity: 0.10 + 0.55 * r.shade }}
      title={bandTitle(cov, months, r)} />
  ))

  if (months.length === 0) {
    return <div className="card"><h2>{title}</h2><div className="empty">No dated comments.</div></div>
  }
  const ticks = axisTicks(months)

  return (
    <div className="card">
      <div className="card-head">
        <h2>{title}</h2>
        <div className="tl-opts">
          <label className="filter" title="Draw only the months this person was writing in">
            <input type="checkbox" checked={zoom} onChange={(e) => setZoom(e.target.checked)} />
            Zoom to active period
          </label>
          {multi && (
            <label className="filter" title="One row per nickname instead of one stacked row">
              <input type="checkbox" checked={split} onChange={(e) => setSplit(e.target.checked)} />
              One lane per nickname
            </label>
          )}
          {cov.size > 0 && (
            <label className="filter" title="Shade months the corpus only partly covers">
              <input type="checkbox" checked={bands} onChange={(e) => setBands(e.target.checked)} />
              Show coverage
            </label>
          )}
        </div>
      </div>

      {showSplit ? (
        nicks.map((nick, i) => (
          <div className="tl-row" key={nick}>
            <div className="tl-label" title={nick}>{nick}</div>
            <div className="timeline fill banded">
              {bandEls}
              {months.map((m) => {
                const n = byNick.get(nick).get(m) || 0
                return <span key={m} className="bar"
                  style={{ height: `${(n / lanePeak) * 100}%`,
                           background: n ? colourFor(i) : 'transparent' }}
                  title={`${nick} · ${m}: ${n}`} />
              })}
            </div>
          </div>
        ))
      ) : (
        <div className="timeline fill banded">
          {bandEls}
          {months.map((m) => {
            const tot = totalAt(m)
            return (
              <span key={m} className="bar stackbar"
                style={{ height: `${(tot / peak) * 100}%`, background: 'transparent' }}
                title={`${m}: ${tot}`}>
                {nicks.map((nick, i) => {
                  const n = byNick.get(nick).get(m) || 0
                  if (!n) return null
                  return <span key={nick} className="seg"
                    style={{ height: `${(n / tot) * 100}%`, background: colourFor(i) }}
                    title={`${nick} · ${m}: ${n}`} />
                })}
              </span>
            )
          })}
        </div>
      )}

      <div className="tl-axis full">
        {ticks.map(({ m }) => <span key={m}>{m}</span>)}
      </div>

      {multi && !showSplit && (
        <div className="tl-legend">
          {nicks.map((nick, i) => (
            <span key={nick} className="key">
              <i style={{ background: colourFor(i) }} />{nick}
            </span>
          ))}
        </div>
      )}
      {bands && runs.length > 0 && (
        <div className="tl-legend">
          <span className="key"><i className="band" style={{ opacity: 0.65 }} />Little or nothing held</span>
          <span className="key"><i className="band" style={{ opacity: 0.25 }} />Partly held</span>
          <span className="key subtle">Shaded months are gaps in the corpus, not necessarily silence.</span>
        </div>
      )}
      {!zoom && corpusLo && (
        <p className="subtle tl-note">
          Axis spans the whole corpus ({monthOf(corpusLo)} to {monthOf(corpusHi)}), so
          profiles can be read against each other. Empty months are silence, not absence of data.
        </p>
      )}
    </div>
  )
}

// A month the coverage table says nothing about is drawn as strongly as a
// month it says is empty. Not knowing how much is missing is not reassurance.
const UNKNOWN = 0.85

function bandTitle(cov, months, r) {
  const a = months[r.from], b = months[r.to]
  const when = a === b ? a : `${a} to ${b}`
  const c = cov.get(a)
  if (!c || c.coverage === null) return `${when}: coverage unknown — no reliable denominator`
  const pct = Math.round(c.coverage * 100)
  const of = c.published ? ` (${c.held} of ~${c.published} articles)` : ''
  return `${when}: about ${pct}% of the paper held${of} — a gap here may be ours`
}

const sum = (m) => [...m.values()].reduce((a, b) => a + b, 0)
const min = (a, b) => (!a ? b : !b ? a : a < b ? a : b)
const max = (a, b) => (!a ? b : !b ? a : a > b ? a : b)

// One shared fetch of the corpus span: it is the same answer for every
// profile, and refetching it per view would be a request per click.
let spanPromise = null
export function useCorpusSpan(send) {
  const [span, setSpan] = useState(null)
  useEffect(() => {
    if (!spanPromise) {
      spanPromise = send('dataset_stats')
        .then((r) => (r.ok ? r.comment_span : null)).catch(() => null)
    }
    let live = true
    spanPromise.then((s) => { if (live) setSpan(s) })
    return () => { live = false }
  }, [send])
  return span
}

// Coverage for the titles this profile actually writes in. Cached per title
// set, because a commenter reopened is the same question asked twice.
const covCache = new Map()
export function useCoverage(send, journals) {
  const key = [...new Set(journals || [])].sort().join(',')
  const [rows, setRows] = useState(null)
  useEffect(() => {
    if (!key) { setRows(null); return }
    if (!covCache.has(key)) {
      covCache.set(key, send('coverage_timeline', { journals: key.split(',') })
        .then((r) => (r.ok ? r.months : [])).catch(() => []))
    }
    let live = true
    covCache.get(key).then((m) => { if (live) setRows(m) })
    return () => { live = false }
  }, [key, send])
  return rows
}
