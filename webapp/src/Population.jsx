import { useEffect, useState } from 'react'

// The population view: what the profiled commenters look like taken together.
// This is the sociological output of the study, so the denominators matter as
// much as the shares — "unknown" is reported as a real category, never hidden,
// because most subjects genuinely leave no evidence of gender or region.

const LEANING_ORDER = ['far-left', 'left', 'centre-left', 'centre',
                       'centre-right', 'right', 'far-right', 'mixed', 'unclear']
const MASTERY_ORDER = ['native-fluent', 'fluent', 'good', 'approximate', 'poor']

export default function Population({ send, onNick, onPersona }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    let live = true
    send('profile_overview').then((r) => {
      if (!live) return
      if (r.ok) setD(r); else setErr(r.error)
    }).catch(() => {})
    return () => { live = false }
  }, [send])

  if (err) return <div className="banner warn">{err}</div>
  if (!d) return <div className="empty">Loading…</div>
  if (!d.profiles) return (
    <div className="empty">
      No profiles yet. Run the analysis pass to build them.
    </div>
  )

  const open = (r) => r.subject_kind === 'persona'
    ? onPersona?.(Number(r.subject_key))
    : onNick?.(r.subject_key)

  return (
    <>
      <div className="card">
        <h2>Profiled population</h2>
        <div className="metrics">
          <Metric k="Subjects profiled" v={d.profiles} />
          <Metric k="Of which people (merged)" v={d.personas} />
        </div>
        <p className="subtle" style={{ marginTop: 12 }}>
          One subject is one writer: a person where nicknames have been linked,
          otherwise a single nickname. Shares below are of profiled subjects, not
          of comments — a prolific commenter counts once.
        </p>
      </div>

      <div className="row2">
        <Dist title="Gender" rows={d.gender} field="g" total={d.profiles}
          note="Read only from French grammatical self-reference. Most writers never refer to themselves in a gendered way, so 'unknown' is expected to dominate." />
        <Dist title="Language mastery" rows={d.mastery} field="mastery" total={d.profiles}
          order={MASTERY_ORDER}
          note="Command of grammar, syntax and vocabulary. Not typing accents is an input habit and is never counted against mastery." />
      </div>

      <div className="row2">
        <Dist title="Political leaning" rows={d.politics} field="leaning" total={d.profiles}
          order={LEANING_ORDER}
          note="'unclear' means the comments carried no usable position — it is not a centre reading." />
        <Dist title="Linguistic region" rows={d.region} field="region" total={d.profiles}
          note="From helvetisms and local knowledge. Romandie-unspecified is the honest answer for most." />
      </div>

      {(d.drifters || []).length > 0 && (
        <div className="card">
          <h2>Changed position over time</h2>
          <p className="subtle">
            Subjects whose recorded stance moves across periods. This is the
            trajectory the study is looking for, and it is rare — check each
            against the comments before treating it as a finding.
          </p>
          <div className="table-wrap"><table>
            <thead><tr><th>Subject</th><th>Trajectory</th></tr></thead>
            <tbody>{d.drifters.map((r) => (
              <tr key={r.subject_kind + r.subject_key} className="rowlink" onClick={() => open(r)}>
                <td><strong>{r.label}</strong></td>
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

      <div className="card">
        <h2>Most prolific profiled subjects</h2>
        <div className="table-wrap"><table>
          <thead><tr>
            <th>Subject</th><th className="num">Comments</th><th>Mastery</th>
            <th className="num">Err / 100w</th><th>Leaning</th><th>Drift</th>
          </tr></thead>
          <tbody>{(d.top || []).map((r) => (
            <tr key={r.subject_kind + r.subject_key} className="rowlink" onClick={() => open(r)}>
              <td><strong>{r.label}</strong>
                {r.subject_kind === 'persona' && <span className="chip">person</span>}</td>
              <td className="num">{r.n_comments}</td>
              <td>{r.mastery || '—'}</td>
              <td className="num">{r.err != null ? r.err.toFixed(2) : '—'}</td>
              <td>{r.leaning || '—'}</td>
              <td>{r.drift && r.drift !== 'none' ? r.drift : <span className="subtle">—</span>}</td>
            </tr>
          ))}</tbody>
        </table></div>
      </div>
    </>
  )
}

function Dist({ title, rows, field, total, note, order }) {
  let data = rows || []
  if (order) {
    data = [...data].sort((a, b) => {
      const ia = order.indexOf(a[field]), ib = order.indexOf(b[field])
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
    })
  }
  const peak = Math.max(1, ...data.map((r) => Number(r.count)))
  return (
    <div className="card">
      <h2>{title}</h2>
      {data.length === 0 ? <div className="empty">No data.</div> : data.map((r) => {
        const n = Number(r.count)
        return (
          <div className="distrow" key={String(r[field])}>
            <span className="lab">{r[field] || 'unknown'}</span>
            <span className="track"><span className="fill" style={{ width: `${(n / peak) * 100}%` }} /></span>
            <span className="n">{n}</span>
            <span className="pct">{total ? `${Math.round((n / total) * 100)}%` : ''}</span>
          </div>
        )
      })}
      {note && <p className="subtle" style={{ marginTop: 10 }}>{note}</p>}
    </div>
  )
}

function Metric({ k, v }) {
  return <div className="metric"><div className="v">{v}</div><div className="k">{k}</div></div>
}
