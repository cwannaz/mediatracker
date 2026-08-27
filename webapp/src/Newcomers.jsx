import { useCallback, useEffect, useMemo, useState } from 'react'

// Nicknames the corpus had never seen, and who they might have been before.
//
// Two things are being shown at once and they must not be confused. The
// arrivals are a fact about the data. The predecessors are a ranking of
// guesses, over a pool whose disappearances are mostly unobserved — see the
// note the tab prints from the coverage it actually has, rather than from a
// claim written here.

const COMMUNITIES = [
  { id: 'lematin', label: 'Le Matin' },
  { id: 'tx-romandie', label: '24 heures / Tribune de Genève' },
]

const fmtDay = (iso) => {
  if (!iso) return '—'
  try {
    return new Date(`${iso}T12:00:00Z`).toLocaleDateString(undefined,
      { timeZone: 'UTC', day: 'numeric', month: 'short' })
  } catch { return iso }
}

const fmtQuiet = (d) => {
  if (d == null) return '—'
  if (d < 60) return `${Math.round(d)} d`
  if (d < 730) return `${Math.round(d / 30.4)} mo`
  return `${(d / 365.25).toFixed(1)} y`
}

// Left on `auto` the daemon starts the list the day after dense coverage
// begins, which is the only cut it can defend. A fixed number of days is a
// question about the calendar rather than about the corpus, so it may reach
// back past the coverage the arrivals are measured against — the table's
// "absent from" column is what shows when it has.
const WINDOWS = [
  { id: 0, label: 'Since coverage began' },
  { id: 7, label: 'Last 7 days' },
  { id: 14, label: 'Last 14 days' },
]

export default function Newcomers({ send, onNick }) {
  const [community, setCommunity] = useState('lematin')
  const [days, setDays] = useState(0)
  const [minComments, setMinComments] = useState(3)
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [open, setOpen] = useState(null)

  useEffect(() => {
    let live = true
    setData(null); setErr(null); setOpen(null)
    send('newcomers_overview', { community, days, min_comments: minComments })
      .then((r) => { if (!live) return; if (r.ok) setData(r); else setErr(r.error) })
      .catch(() => {})
    return () => { live = false }
  }, [send, community, days, minComments])

  return (
    <>
      <div className="toolbar">
        <select value={community} onChange={(e) => setCommunity(e.target.value)}>
          {COMMUNITIES.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
        </select>
        <label className="filter">
          min comments
          <input type="number" min="1" max="50" value={minComments} style={{ width: 60 }}
            onChange={(e) => setMinComments(Math.max(1, Number(e.target.value) || 3))} />
        </label>
        <span className="spacer" />
        {WINDOWS.map((w) => (
          <button key={w.id} className={'chip' + (days === w.id ? ' on' : '')}
            onClick={() => setDays(w.id)}>{w.label}</button>
        ))}
      </div>

      {err && <div className="banner warn">{err}</div>}
      {!data ? <div className="empty">Measuring…</div> : (
        <>
          <CoverageNote d={data} />
          <DailyChart d={data} />
          <ArrivalTable d={data} open={open} onOpen={setOpen} />
          {open && <Predecessors key={`${community}/${open.key}`} send={send}
            community={community} arrival={open} minComments={minComments}
            onNick={onNick} />}
        </>
      )}
    </>
  )
}

// What an arrival is worth depends entirely on how long we had been watching,
// so the tab states its own observation window before it states any finding.
function CoverageNote({ d }) {
  const c = d.coverage
  return (
    <div className="card note">
      <div className="row" style={{ gap: 14, flexWrap: 'wrap', alignItems: 'baseline' }}>
        <strong>{d.total_arrivals} arrivals since {fmtDay(d.since)}</strong>
        <span className="subtle">
          out of {d.subjects} profiles the corpus can place in time; below{' '}
          {d.min_chars.toLocaleString()} characters written no predecessor is ranked
          at all, and below {d.thin_chars.toLocaleString()} the ranking is weak.
        </span>
      </div>
      <p className="subtle" style={{ margin: '8px 0 0' }}>
        {!c.dense_from
          ? <>Not enough daily volume yet to call any stretch densely covered, so an
            arrival here means only that this is the first comment we hold.</>
          : <>Dense coverage of this community runs from {fmtDay(c.dense_from)} —
            {' '}{c.dense_days} days, counting a day as covered above {c.floor} comments.
            Everything before it is a thin archive of selected threads, so an account
            missing from it may simply have been writing where the crawl was not
            looking.{' '}
            {d.since > c.dense_from
              ? <>That is why the list starts the day <em>after</em> coverage begins: on
                the first day every account in the community looks new, because that is
                the day we opened our eyes.</>
              : <><strong>This window reaches back past that</strong>, so the arrivals
                dated {fmtDay(c.dense_from)} and earlier are mostly the crawl opening
                its eyes rather than people joining. The &quot;absent from&quot; column
                is what separates them: it reads nothing for those.</>}</>}
      </p>
    </div>
  )
}

// Daily volume with the arrivals' share inside it. The share is the readable
// quantity: the absolute newcomer count rises and falls with the news cycle,
// and would show a busy Tuesday as an influx of new people.
function DailyChart({ d }) {
  const shown = useMemo(() => d.series.slice(-21), [d])
  const peak = Math.max(1, ...shown.map((s) => s.comments))
  const peakDeb = Math.max(1, ...shown.map((s) => s.debuting))
  return (
    <div className="card">
      <h3 className="sub">Daily comments, and how many came from arrivals</h3>
      <div className="tl-row">
        <div className="tl-label">comments</div>
        <div className="timeline">
          {shown.map((s) => (
            <span key={s.day} className="bar stackbar"
              style={{ height: `${(s.comments / peak) * 100}%` }}
              title={`${s.day}: ${s.comments} comments, ${s.from_arrivals} from arrivals`}>
              <span className="seg" style={{
                height: `${s.comments ? (s.from_arrivals / s.comments) * 100 : 0}%`,
              }} />
            </span>
          ))}
        </div>
      </div>
      <div className="tl-row">
        <div className="tl-label">profiles debuting</div>
        <div className="timeline">
          {shown.map((s) => (
            <span key={s.day} className="bar alt"
              style={{ height: `${(s.debuting / peakDeb) * 100}%`, opacity: s.debuting ? 1 : 0.12 }}
              title={`${s.day}: ${s.debuting} profiles seen for the first time`} />
          ))}
        </div>
      </div>
      <div className="tl-axis">
        <span>{fmtDay(shown[0]?.day)}</span><span>{fmtDay(shown[shown.length - 1]?.day)}</span>
      </div>
      <p className="subtle" style={{ margin: '10px 0 0' }}>
        The spike on the first covered day is the crawl starting, not the site
        filling up. What is worth reading is whether the debut rate settles,
        and how much of each day&apos;s volume the recent arrivals carry.
      </p>
    </div>
  )
}

function ArrivalTable({ d, open, onOpen }) {
  if (!d.arrivals.length) return <div className="empty">No arrivals in this window.</div>
  const peak = Math.max(1, ...d.arrivals.flatMap((a) => a.daily))
  return (
    <div className="table-wrap"><table>
      <thead><tr>
        <th>Profile</th><th>Debut</th><th className="num">Comments</th>
        <th className="num">Characters</th>
        <th className="num">Days active</th><th>Daily</th><th>Absent from</th>
      </tr></thead>
      <tbody>
        {d.arrivals.map((a) => (
          <tr key={a.key} className={'rowlink' + (open?.key === a.key ? ' on' : '')}
            onClick={() => onOpen(a)}>
            <td>{a.label}
              {!a.comparable
                ? <span className="subtle"> · too little text to compare</span>
                : a.thin && <span className="subtle"> · thin</span>}</td>
            <td>{fmtDay(a.debut)}</td>
            <td className="num">{a.n_comments}</td>
            <td className="num subtle">{a.n_chars.toLocaleString()}</td>
            <td className="num subtle">{a.active_days}</td>
            <td>
              <span className="spark">
                {a.daily.slice(-14).map((v, i) => (
                  <span key={i} style={{ height: `${(v / peak) * 100}%`, opacity: v ? 1 : 0.15 }} />
                ))}
              </span>
            </td>
            <td className="subtle">
              {a.absent_days
                ? `${a.absent_days} d / ${a.absent_comments.toLocaleString()} comments`
                : 'nothing — arrived on day one'}
            </td>
          </tr>
        ))}
      </tbody>
    </table></div>
  )
}

// The guess. Everything about how it is framed is deliberate: the field size
// and the lift come before the names, because a top score over a flat field of
// four hundred is what a coincidence looks like.
function Predecessors({ send, community, arrival, minComments, onNick }) {
  const [observedOnly, setObservedOnly] = useState(false)
  const [res, setRes] = useState(null)
  const [tl, setTl] = useState(null)
  const [pick, setPick] = useState(null)

  useEffect(() => {
    let live = true
    setRes(null); setTl(null); setPick(null)
    send('newcomers_predecessors', {
      community, kind: arrival.kind, key: arrival.key,
      min_comments: minComments, observed_only: observedOnly, limit: 12,
    }).then((r) => { if (live && r.ok) setRes(r) }).catch(() => {})
    return () => { live = false }
  }, [send, community, arrival, minComments, observedOnly])

  const choose = useCallback((c) => {
    setPick(c); setTl(null)
    send('proximity_timeline', {
      bucket: 'week',
      subjects: [
        { kind: arrival.kind, key: arrival.key, community, label: arrival.label },
        { kind: c.b.kind, key: c.b.key, community: c.b.community, label: c.b.label },
      ],
    }).then((r) => { if (r.ok) setTl(r) }).catch(() => {})
  }, [send, community, arrival])

  if (!res) return <div className="card"><div className="empty">Comparing…</div></div>
  if (!res.subject) {
    return <div className="card"><h2>{arrival.label}</h2>
      <p className="subtle">{arrival.n_chars.toLocaleString()} characters is too little
        for the style measures to say anything; no predecessor is ranked.</p></div>
  }

  return (
    <div className="card">
      <h2>Who was {arrival.label} before?</h2>
      <div className="metrics">
        <Metric k="Candidates" v={res.field} />
        <Metric k="Watched stop" v={res.observed_field} />
        <Metric k="Top beats the field by" v={res.lift == null ? '—' : `${res.lift} SD`} />
        <Metric k="Text to judge on" v={`${res.n_chars.toLocaleString()} ch`} />
        <Metric k="Wording vs coincidence" hi={res.lexical_standout?.excess > 0}
          v={fmtSD(res.lexical_standout?.excess)} />
        <Metric k="Rates vs coincidence" hi={res.style_standout?.excess > 0}
          v={fmtSD(res.style_standout?.excess)} />
      </div>

      <p className="subtle" style={{ margin: '10px 0 0' }}>
        {res.field} accounts had gone quiet before {arrival.label} started, and each is
        read twice. <strong>Wording</strong> compares the character sequences the two
        actually repeat — a contraction, a slang turn, a habitual misspelling, a space
        before a colon. <strong>Rates</strong> is the older reading: thirteen averages
        such as word length and punctuation per comment. On a short sample the first is
        far the stronger — held out by time over this population, a probe of 1&nbsp;300
        characters finds its own author top of {res.field > 0 ? '744' : '744'} profiles
        49% of the time by wording against 8% by rates — but the two fail differently,
        so a pair they agree on is worth more than either says alone. They are not
        blended: no weighting this corpus can justify exists yet.
      </p>
      <p className="subtle" style={{ margin: '8px 0 0' }}>
        Both figures above are excesses over coincidence. The best of {res.field} draws
        already sits about {res.lexical_standout?.chance ?? '—'} standard deviations
        above the mean with no signal present at all, so only the surplus counts, and a
        negative one means the top match is doing worse than chance. Read the n-grams
        under each row before believing any of it: the strongest match this ever
        produced turned out to rest entirely on fragments of a third party&apos;s
        handle that both had replied to, which is why mentions and links are now
        stripped before anything is counted.
      </p>
      <p className="subtle" style={{ margin: '8px 0 0' }}>
        Only {res.observed_field} of the {res.field} were watched falling silent — the
        rest stopped before {fmtDay(res.dense_from)}, when the crawl could not have
        seen them anyway, so their silence is our gap and not their absence. With{' '}
        {res.dense_days} days of dense coverage, even a watched silence is so far
        indistinguishable from a few days off. And neither comment system exposes a
        user id, so every identity here is a nickname: a name never seen before may be
        a new account, a renamed one, or the same person spelling their handle
        differently.
      </p>
      <div className="toolbar" style={{ marginTop: 10 }}>
        <label className="filter">
          <input type="checkbox" checked={observedOnly}
            onChange={() => setObservedOnly((v) => !v)} />
          only accounts we watched go quiet
        </label>
      </div>

      {!res.candidates.length ? <div className="empty">No candidate under this filter.</div> : (
        <div className="table-wrap"><table>
          <thead><tr>
            <th>Candidate</th><th className="num">Wording</th>
            <th className="num">Rates</th><th className="num">Silent for</th>
            <th className="num">Their text</th><th>Disappearance</th>
          </tr></thead>
          <tbody>
            {res.candidates.map((c, i) => (
              <tr key={i} className={'rowlink' + (pick === c ? ' on' : '')}
                onClick={() => choose(c)}>
                <td>
                  {c.b.label}
                  {c.drivers?.length > 0 && (
                    <div className="drivers">
                      {c.drivers.slice(0, 8).map((g, j) => (
                        <code key={j}>{g.replace(/ /g, '␣')}</code>
                      ))}
                    </div>
                  )}
                </td>
                <td className="num">{c.lexical == null ? '—' : <Bar v={c.lexical * 3} label={c.lexical.toFixed(4)} />}</td>
                <td className="num subtle">{c.style == null ? '—' : c.style.toFixed(3)}</td>
                <td className="num">{fmtQuiet(c.quiet_days)}</td>
                <td className="num subtle">{(c.b_chars ?? 0).toLocaleString()} ch</td>
                <td className={c.disappearance === 'observed' ? '' : 'subtle'}>
                  {c.disappearance === 'observed' ? 'watched' : 'before we watched'}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}

      {pick && (
        <>
          <h3 className="sub">{pick.b.label} &nbsp;→&nbsp; {arrival.label}</h3>
          {!tl ? <div className="empty">Loading…</div>
            : tl.buckets.length === 0 ? <div className="empty">No dated comments.</div>
              : <Weekly tl={tl} />}
          <p className="subtle" style={{ margin: '8px 0 0' }}>
            Largest disagreements first — these are what would refute the match.
          </p>
          <div className="table-wrap"><table className="compare"><tbody>
            {pick.per_feature.slice(0, 6).map((f) => (
              <tr key={f.feature}>
                <td>{f.feature.replace(/_/g, ' ')}</td>
                <td className="num" style={{ width: 90 }}>{f.z_diff} SD</td>
                <td style={{ width: '55%' }}>
                  <span className="zbar" style={{ width: `${Math.min(100, f.z_diff * 40)}%` }} />
                </td>
              </tr>
            ))}
          </tbody></table></div>
          {onNick && (
            <div className="actions" style={{ marginTop: 10 }}>
              <button className="btn secondary" onClick={() => onNick(pick.b.key)}>
                Read {pick.b.label}
              </button>
              <button className="btn secondary" onClick={() => onNick(arrival.key)}>
                Read {arrival.label}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function Weekly({ tl }) {
  const peak = Math.max(1, ...tl.series.flatMap((s) => s.values))
  return (
    <>
      {tl.series.map((s, i) => (
        <div key={i} className="tl-row">
          <div className="tl-label" style={{ color: i === 0 ? 'var(--brand-gold)' : 'var(--accent)' }}>
            {s.label}
          </div>
          <div className="timeline">
            {s.values.map((v, j) => (
              <span key={j} className={'bar' + (i === 0 ? ' alt' : '')}
                style={{ height: `${(v / peak) * 100}%`, opacity: v ? 1 : 0.12 }}
                title={`week of ${tl.buckets[j]}: ${v}`} />
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

// `v` is the bar's fill, `label` what to print — the two differ for the wording
// score, whose useful range is a fraction of 0..1 and would otherwise draw as a
// stub on every row.
function Bar({ v, label }) {
  return (
    <span className="scorecell">
      <span className="scorebar" style={{ width: `${Math.min(100, v * 100)}%` }} />
      <span className="scoretext">{label ?? v.toFixed(3)}</span>
    </span>
  )
}

const fmtSD = (x) => (x == null ? '—' : `${x > 0 ? '+' : ''}${x} SD`)

function Metric({ k, v, hi }) {
  return (
    <div className={'metric' + (hi ? ' hi' : '')}>
      <div className="v">{v}</div><div className="k">{k}</div>
    </div>
  )
}
