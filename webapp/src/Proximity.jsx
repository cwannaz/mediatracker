import { useCallback, useEffect, useMemo, useState } from 'react'

// Which two accounts write alike.
//
// The ranking is stylometric only — the deterministic metrics, never the
// LLM-inferred fields, which were produced by reading the same text. Rhythm
// (when someone posts) and the activity overlap are shown beside it but do not
// move the order: see the calibration note, which is measured rather than
// asserted.
//
// Nothing here concludes anything. It produces a shortlist for a human.

const SORTS = [
  { id: 'lexical', label: 'Wording' },
  { id: 'score', label: 'Rates' },
  { id: 'rhythm', label: 'Rhythm' },
  { id: 'gap', label: 'Shortest gap' },
]

const COMMUNITIES = [
  { id: '', label: 'All communities' },
  { id: 'lematin', label: 'Le Matin' },
  { id: 'tx-romandie', label: '24 heures / Tribune de Genève' },
]

export default function Proximity({ send }) {
  const [community, setCommunity] = useState('lematin')
  const [minComments, setMinComments] = useState(8)
  const [successionOnly, setSuccessionOnly] = useState(false)
  const [sort, setSort] = useState('lexical')
  const [data, setData] = useState(null)
  const [calib, setCalib] = useState(null)
  const [err, setErr] = useState(null)
  const [open, setOpen] = useState(null)      // the selected pair
  const [timeline, setTimeline] = useState(null)

  useEffect(() => {
    let live = true
    setData(null); setErr(null); setOpen(null); setTimeline(null)
    send('proximity_pairs', {
      community: community || null, min_comments: minComments,
      succession_only: successionOnly, sort, limit: 150,
    }).then((r) => {
      if (!live) return
      if (r.ok) setData(r); else setErr(r.error)
    }).catch(() => {})
    return () => { live = false }
  }, [send, community, minComments, successionOnly, sort])

  useEffect(() => {
    let live = true
    send('proximity_calibration', { min_comments: minComments })
      .then((r) => { if (live && r.ok) setCalib(r) }).catch(() => {})
    return () => { live = false }
  }, [send, minComments])

  const select = useCallback((p) => {
    setOpen(p); setTimeline(null)
    send('proximity_timeline', {
      subjects: [p.a, p.b].map((s) => ({
        kind: s.kind, key: s.key, community: s.community, label: s.label,
      })),
    }).then((r) => { if (r.ok) setTimeline(r) }).catch(() => {})
  }, [send])

  return (
    <>
      <div className="toolbar">
        <select value={community} onChange={(e) => setCommunity(e.target.value)}>
          {COMMUNITIES.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
        </select>
        <label className="filter">
          min comments
          <input type="number" min="3" max="100" value={minComments} style={{ width: 64 }}
            onChange={(e) => setMinComments(Math.max(3, Number(e.target.value) || 8))} />
        </label>
        <label className="filter">
          <input type="checkbox" checked={successionOnly}
            onChange={() => setSuccessionOnly((v) => !v)} />
          never active at the same time
        </label>
        <span className="spacer" />
        <span className="subtle">sort</span>
        {SORTS.map((s) => (
          <button key={s.id} className={'chip' + (sort === s.id ? ' on' : '')}
            onClick={() => setSort(s.id)}>{s.label}</button>
        ))}
      </div>

      <Calibration calib={calib} />

      {err && <div className="banner warn">{err}</div>}
      {!data ? <div className="empty">Comparing…</div> : (
        <>
          <p className="subtle" style={{ margin: '0 0 10px' }}>
            {data.compared.toLocaleString()} pairs compared over {data.subjects} subjects;
            the {Math.min(data.pairs.length, 150)} closest shown.
          </p>
          <div className="table-wrap"><table>
            <thead><tr>
              <th>Subject</th><th>Subject</th>
              <th className="num">Wording</th><th className="num">Rates</th>
              <th className="num">Rhythm</th><th className="num">Overlap / gap</th>
              <th className="num">Comments</th>
            </tr></thead>
            <tbody>
              {data.pairs.map((p, i) => (
                <tr key={i} className={'rowlink' + (open === p ? ' on' : '')}
                  onClick={() => select(p)}>
                  <td>{p.a.label}</td>
                  <td>{p.b.label}</td>
                  <td className="num">
                    {p.lexical == null ? '—'
                      : <Bar v={p.lexical * 3} label={p.lexical.toFixed(4)} />}
                  </td>
                  <td className="num subtle">{p.style.toFixed(3)}</td>
                  <td className="num">{p.rhythm == null ? '—' : p.rhythm.toFixed(2)}</td>
                  <td className="num">
                    {p.gap_days != null
                      ? <span className="gap">gap {fmtDays(p.gap_days)}</span>
                      : p.overlap_days ? <span className="subtle">{fmtDays(p.overlap_days)} together</span> : '—'}
                  </td>
                  <td className="num subtle">{p.a.n_comments} / {p.b.n_comments}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </>
      )}

      {open && <PairDetail pair={open} timeline={timeline} />}
    </>
  )
}

// The tab states its own reliability, recomputed from the confirmed personas
// each time it loads. Quoting a number measured once would go stale silently,
// and this one is expected to move: every persona the user confirms adds pairs.
function Calibration({ calib }) {
  if (!calib) return null
  const s = calib.signals || {}
  const auc = s.score?.auc
  return (
    <div className="card note">
      <div className="row" style={{ gap: 14, flexWrap: 'wrap', alignItems: 'baseline' }}>
        <strong>How much this is worth: AUC {auc == null ? '—' : auc.toFixed(2)}</strong>
        <span className="subtle">
          measured against {calib.personas} confirmed {calib.personas === 1 ? 'person' : 'people'},
          split back into {calib.aliases} separate accounts — {calib.same_pairs} known-same pairs
          against {calib.different_pairs.toLocaleString()} known-different ones.
        </span>
      </div>
      <p className="subtle" style={{ margin: '8px 0 0' }}>
        A same-person pair outranks a random different pair {auc == null ? '—' : Math.round(auc * 100)}% of
        the time, so the ordering carries real signal. But the median same-person
        score ({s.score?.same_median ?? '—'}) sits <em>below</em> the 99th percentile of
        different-person scores ({s.score?.different_p99 ?? '—'}): there is no threshold that
        separates them. Read this as a shortlist to look at, never as a verdict.
        {s.rhythm?.auc != null && <> Rhythm alone scores {s.rhythm.auc.toFixed(2)} and blending it
        into the ranking made the separation worse, so it is shown but not used to sort.</>}
        {' '}That AUC describes the <em>rates</em> column. <strong>Wording</strong> beside it
        is the newer and, on short samples, much stronger reading — shared character
        sequences rather than averages: over 744 profiles a probe of 1&nbsp;300 characters
        finds its own author top of the list 49% of the time by wording against 8% by
        rates. It is not folded into this AUC, which was measured before it existed.
        {calib.same_pairs < 40 && <> With {calib.same_pairs} same-person pairs the error bar on
        that figure is wide — confirming more people in the People tab is what narrows it.</>}
      </p>
    </div>
  )
}

function PairDetail({ pair, timeline }) {
  return (
    <div className="card">
      <h2>{pair.a.label} &nbsp;~&nbsp; {pair.b.label}</h2>
      <div className="metrics">
        <Metric k="Wording" v={pair.lexical == null ? '—' : pair.lexical.toFixed(4)} />
        <Metric k="Rates" v={pair.style.toFixed(3)} />
        <Metric k="Distance" v={`${pair.distance} SD`} />
        <Metric k="Rhythm" v={pair.rhythm == null ? '—' : pair.rhythm.toFixed(3)} />
        <Metric k={pair.gap_days != null ? 'Gap between them' : 'Active together'}
          v={fmtDays(pair.gap_days != null ? pair.gap_days : (pair.overlap_days || 0))} />
        <Metric k="Features compared" v={pair.features_compared} />
      </div>

      <h3 className="sub">Activity on a common timeline</h3>
      {!timeline ? <div className="empty">Loading…</div>
        : timeline.buckets.length === 0 ? <div className="empty">No dated comments.</div>
          : <CommonTimeline tl={timeline} />}

      <h3 className="sub">Where they differ most</h3>
      <p className="subtle" style={{ margin: '0 0 8px' }}>
        Standard deviations apart on each measure, largest first — these are what
        would refute the match, so they are shown before the agreements.
      </p>
      <div className="table-wrap"><table className="compare">
        <tbody>
          {pair.per_feature.map((f) => (
            <tr key={f.feature}>
              <td>{f.feature.replace(/_/g, ' ')}</td>
              <td className="num" style={{ width: 90 }}>{f.z_diff} SD</td>
              <td style={{ width: '55%' }}>
                <span className="zbar" style={{ width: `${Math.min(100, f.z_diff * 40)}%` }} />
              </td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </div>
  )
}

// Both subjects on one axis, each bar scaled to the same peak, so the shape of
// one ending as the other begins is visible without reading any numbers.
function CommonTimeline({ tl }) {
  const peak = useMemo(
    () => Math.max(1, ...tl.series.flatMap((s) => s.values)), [tl])
  return (
    <>
      {tl.series.map((s, i) => (
        <div key={i} className="tl-row">
          <div className="tl-label" style={{ color: i === 0 ? 'var(--accent)' : 'var(--brand-gold)' }}>
            {s.label}
          </div>
          <div className="timeline">
            {s.values.map((v, j) => (
              <span key={j} className={'bar' + (i === 1 ? ' alt' : '')}
                style={{ height: `${(v / peak) * 100}%`, opacity: v ? 1 : 0.12 }}
                title={`${tl.buckets[j]}: ${v}`} />
            ))}
          </div>
        </div>
      ))}
      <div className="tl-axis">
        <span>{tl.buckets[0]}</span><span>{tl.buckets[tl.buckets.length - 1]}</span>
      </div>
    </>
  )
}

function Bar({ v, label }) {
  return (
    <span className="scorecell">
      <span className="scorebar" style={{ width: `${Math.min(100, v * 100)}%` }} />
      <span className="scoretext">{label ?? v.toFixed(3)}</span>
    </span>
  )
}

function Metric({ k, v }) {
  return <div className="metric"><div className="v">{v}</div><div className="k">{k}</div></div>
}

function fmtDays(d) {
  if (d == null) return '—'
  if (d < 60) return `${Math.round(d)} d`
  if (d < 730) return `${Math.round(d / 30.4)} mo`
  return `${(d / 365.25).toFixed(1)} y`
}
