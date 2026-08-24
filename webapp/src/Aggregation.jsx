import { useEffect, useState } from 'react'

const fmtD = (v) => { if (!v) return '—'; try { return new Date(v).toLocaleDateString() } catch { return String(v).slice(0, 10) } }

// Review candidate groups of nicknames that look like the same person, drop the
// members that do not belong, and confirm the rest as one persona. Nothing here
// links anything on its own — every group needs an explicit confirmation.
export default function Aggregation({ send, onPersona }) {
  const [data, setData] = useState(null)
  const [minComments, setMinComments] = useState(2)
  const [busy, setBusy] = useState(null)
  const [excluded, setExcluded] = useState({})   // groupKey -> Set of dropped nicks
  const [labels, setLabels] = useState({})

  const load = (mc = minComments) =>
    send('alias_candidates', { min_comments: Number(mc) })
      .then((r) => { if (r.ok) { setData(r); setExcluded({}); setLabels({}) } })
      .catch(() => {})
  useEffect(() => { load() }, []) // eslint-disable-line

  if (!data) return <div className="empty">Analyzing nicknames…</div>

  const linked = new Set(data.linked || [])
  const keyOf = (g) => g.members.map((m) => m.nick).join('|')

  const toggle = (gk, nick) => setExcluded((e) => {
    const s = new Set(e[gk] || [])
    s.has(nick) ? s.delete(nick) : s.add(nick)
    return { ...e, [gk]: s }
  })

  const linkGroup = async (g) => {
    const gk = keyOf(g)
    const drop = excluded[gk] || new Set()
    const nicks = g.members.map((m) => m.nick).filter((n) => !drop.has(n))
    if (nicks.length < 2) return
    setBusy(gk)
    try {
      await send('link_nicks', {
        nicks,
        label: labels[gk] || g.label,
        confidence: 'confirmed',
        evidence: `confirmed in Aggregation (${g.relation} handle match)`,
      })
      await load()
    } finally { setBusy(null) }
  }

  const pending = (data.strong || []).filter((g) => g.already_linked < g.members.length)
  const done = (data.strong || []).filter((g) => g.already_linked === g.members.length)

  return (
    <>
      <div className="toolbar">
        <span className="subtle">Min comments per nickname</span>
        <input type="number" min="1" style={{ maxWidth: 90 }} value={minComments}
          onChange={(e) => setMinComments(e.target.value)} />
        <button className="btn secondary" onClick={() => load(minComments)}>Re-analyze</button>
        <span className="subtle">
          {pending.length} groups to review · {done.length} already linked · {(data.weak || []).length} weak pairs
        </span>
      </div>

      <p className="subtle" style={{ marginBottom: 14 }}>
        Groups are matched on the nickname alone (accents, case and punctuation folded away).
        This is not stylometry — untick anyone who does not belong before confirming.
      </p>

      {pending.map((g) => {
        const gk = keyOf(g)
        const drop = excluded[gk] || new Set()
        const keep = g.members.filter((m) => !drop.has(m.nick))
        return (
          <div className="card" key={gk}>
            <div className="panel-head">
              <h2 style={{ marginBottom: 0 }}>{g.label}</h2>
              <span className="chip">{g.relation}</span>
              <span className="subtle">
                {g.total_comments} comments · overlap {g.overlap_days}d · {g.shared_articles} shared articles
              </span>
            </div>
            <div className="table-wrap"><table>
              <thead><tr>
                <th>Include</th><th>Nickname</th><th className="num">Comments</th>
                <th>First seen</th><th>Last seen</th><th></th>
              </tr></thead>
              <tbody>
                {g.members.map((m) => (
                  <tr key={m.nick}>
                    <td>
                      <input type="checkbox" checked={!drop.has(m.nick)}
                        onChange={() => toggle(gk, m.nick)} />
                    </td>
                    <td><strong>{m.nick}</strong></td>
                    <td className="num">{m.comments}</td>
                    <td>{fmtD(m.first_seen)}</td>
                    <td>{fmtD(m.last_seen)}</td>
                    <td>{linked.has(m.nick) && <span className="chip">already linked</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
            <div className="actions">
              <input type="text" placeholder="Name for this person" style={{ maxWidth: 260 }}
                value={labels[gk] ?? g.label}
                onChange={(e) => setLabels((l) => ({ ...l, [gk]: e.target.value }))} />
              <button className="btn" disabled={busy === gk || keep.length < 2}
                onClick={() => linkGroup(g)}>
                {busy === gk ? 'Linking…' : `Confirm ${keep.length} as one person`}
              </button>
            </div>
          </div>
        )
      })}

      {done.length > 0 && (
        <div className="card">
          <h2>Already linked</h2>
          {done.map((g) => (
            <div key={keyOf(g)} className="subtle" style={{ padding: '3px 0' }}>
              {g.label} — {g.members.map((m) => m.nick).join(', ')}
            </div>
          ))}
        </div>
      )}

      <div className="card">
        <h2>Weak pairs — judgement needed</h2>
        <p className="subtle">
          Small edit distance (a typo or a changed digit). These are deliberately not grouped:
          chaining them merges unrelated people. Link from a commenter’s Identity card if you
          decide a pair is the same person.
        </p>
        <div className="table-wrap"><table>
          <thead><tr>
            <th>Nickname</th><th className="num">Comments</th><th>Active</th>
            <th>Nickname</th><th className="num">Comments</th><th>Active</th>
            <th className="num">Overlap</th><th className="num">Shared</th>
          </tr></thead>
          <tbody>
            {(data.weak || []).map((w, i) => (
              <tr key={i}>
                <td><strong>{w.a.nick}</strong></td>
                <td className="num">{w.a.comments}</td>
                <td>{fmtD(w.a.first_seen)} → {fmtD(w.a.last_seen)}</td>
                <td><strong>{w.b.nick}</strong></td>
                <td className="num">{w.b.comments}</td>
                <td>{fmtD(w.b.first_seen)} → {fmtD(w.b.last_seen)}</td>
                <td className="num">{w.overlap_days}d</td>
                <td className="num">{w.shared_articles}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </div>
    </>
  )
}
