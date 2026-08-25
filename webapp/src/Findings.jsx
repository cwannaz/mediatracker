import { useEffect, useMemo, useState } from 'react'
import { SECTIONS } from './findings.js'

// The written record of what the study has found.
//
// The prose is authored (findings.js); every number in it is recomputed here
// from the live database. A finding whose figures went stale would be worse
// than no finding at all, so nothing on this page is typed by hand.
//
// Each finding carries how it is known — measured, inferred, observed or open.
// That is the same distinction the database enforces between deterministic
// metrics and LLM-inferred attributes, carried through to the write-up.

const STATUS = {
  measured: { label: 'measured', hint: 'Computed deterministically from the stored text. Reproducible.' },
  inferred: { label: 'inferred', hint: 'An LLM’s reading of a dossier, with quotes and a stated confidence.' },
  observed: { label: 'observed', hint: 'Noticed while reading the corpus. A judgement, with no aggregate behind it.' },
  open: { label: 'open question', hint: 'Stated as a question. Nothing tests it yet.' },
}

export default function Findings({ connected, send, route, navigate }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  // The open section is part of the URL, so a section can be linked to and Back
  // steps between sections rather than out of the app.
  const active = SECTIONS.some((s) => s.id === route[0]) ? route[0] : SECTIONS[0].id

  useEffect(() => {
    if (!connected) return
    let live = true
    send('findings_overview').then((r) => {
      if (!live) return
      if (r.ok) setData(r); else setErr(r.error)
    }).catch(() => {})
    return () => { live = false }
  }, [connected, send])

  const counts = useMemo(
    () => Object.fromEntries(SECTIONS.map((s) => [s.id, s.findings.length])), [])
  const total = useMemo(
    () => SECTIONS.reduce((a, s) => a + s.findings.length, 0), [])

  if (!connected) return <div className="empty">Daemon offline.</div>
  if (err) return <div className="banner warn">{err}</div>
  if (!data) return <div className="empty">Loading findings…</div>

  const section = SECTIONS.find((s) => s.id === active) || SECTIONS[0]

  return (
    <>
      <nav className="subtabs" role="tablist" aria-label="Findings sections">
        {SECTIONS.map((s) => (
          <button key={s.id} className="subtab" role="tab" aria-selected={active === s.id}
            onClick={() => navigate([s.id])}>
            {s.title}
            <span className="count">{counts[s.id]}</span>
          </button>
        ))}
      </nav>

      <div className="browser findings">
        <div className="card note">
          <div className="row" style={{ gap: 14, flexWrap: 'wrap', alignItems: 'baseline' }}>
            <strong>{total} findings recorded</strong>
            <span className="subtle">
              Prose is written; every figure below is recomputed from the database
              each time this page loads, so nothing here can go quietly stale.
            </span>
          </div>
          <div className="row legend" style={{ gap: 14, marginTop: 10, flexWrap: 'wrap' }}>
            {Object.entries(STATUS).map(([k, v]) => (
              <span key={k} className="row" style={{ gap: 6 }}>
                <span className={`status ${k}`}>{v.label}</span>
                <span className="subtle">{v.hint}</span>
              </span>
            ))}
          </div>
        </div>

        <div className="card">
          <h2>{section.title}</h2>
          <p className="subtle" style={{ margin: '0 0 4px' }}>{section.blurb}</p>
        </div>

        {section.findings.map((f) => <Finding key={f.id} f={f} d={data} />)}
      </div>
    </>
  )
}

function Finding({ f, d }) {
  // Each renderer is optional; a finding may be prose only.
  const figures = f.figures ? safe(() => f.figures(d), []) : []
  const table = f.table ? safe(() => f.table(d), null) : null
  const list = f.list ? safe(() => f.list(d), []) : []

  return (
    <article className="card finding">
      <header className="row" style={{ gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <span className={`status ${f.status}`}>{STATUS[f.status]?.label || f.status}</span>
        <h3>{f.claim}</h3>
        <span className="spacer" />
        <span className="subtle recorded">recorded {f.recorded}</span>
      </header>

      {f.body?.map((p, i) => <p key={i} className="finding-body">{p}</p>)}

      {!!figures.length && (
        <div className="metrics">
          {figures.map((x, i) => (
            <div className={'metric' + (x.hi ? ' hi' : '')} key={i}>
              <div className="v">{x.v}</div>
              <div className="k">{x.k}</div>
            </div>
          ))}
        </div>
      )}

      {table && !!table.rows?.length && (
        <div className="table-wrap"><table className="compare">
          <thead>
            <tr>
              <th />
              {table.cols.map((c) => <th key={c} className="num">{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((r) => (
              <tr key={r.label}>
                <td>{r.label}</td>
                {r.cells.map((c, i) => (
                  <td key={i} className="num">
                    {c.text != null ? c.text : c.n}
                    {c.pct != null && <span className="subtle pct"> {c.pct}</span>}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table></div>
      )}

      {!!list.length && (
        <>
          {f.listTitle && <p className="subtle listtitle">{f.listTitle}</p>}
          <ul className="findlist">
            {list.map((x, i) => (
              <li key={i}><strong>{x.label}</strong> <span className="subtle">{x.detail}</span></li>
            ))}
          </ul>
        </>
      )}

      {!!f.evidence?.length && (
        <div className="evidence">
          {f.evidence.map((e, i) => (
            <blockquote key={i}>
              <p>« {e.quote} »</p>
              <cite>{e.who}{e.note ? ` — ${e.note}` : ''}</cite>
            </blockquote>
          ))}
        </div>
      )}

      {f.caveat && <p className="caveat">{f.caveat}</p>}
    </article>
  )
}

// A finding must never take the page down because its figure function met
// data it did not expect — a missing community, an empty distribution.
function safe(fn, fallback) {
  try { return fn() ?? fallback } catch { return fallback }
}
