import { useCallback, useEffect, useState } from 'react'
import { ReferenceCard } from './Reference.jsx'

// The inferred half of a subject's profile: gender, language mastery, politics
// (with drift), philosophy, region and milieu. Everything here is an ESTIMATE produced
// by the profiling pass, so each block shows its confidence and the verbatim
// quotes it rests on. Deterministic style measures live in their own card and
// are deliberately not mixed in.

const LEANINGS = ['far-left', 'left', 'centre-left', 'centre',
                  'centre-right', 'right', 'far-right']

const COMMUNITIES = { lematin: 'Le Matin', 'tx-romandie': '24 heures / Tribune de Genève' }
const MIN_COMMENTS = 5
const POLL_MS = 5000

export default function ProfilePanel({ nick, personaId, send }) {
  const [state, setState] = useState({ loading: true, profile: null })
  // One analysable subject per comment community: a nickname on Le Matin and
  // the same nickname on 24 heures are two subjects with two profiles.
  const [subjects, setSubjects] = useState([])
  const [community, setCommunity] = useState(null)
  const [reload, setReload] = useState(0)

  const loadSubjects = useCallback(() => {
    const args = personaId != null ? { persona_id: personaId } : { nick }
    return send('profile_subjects', args).then((r) => (r.ok ? r.subjects : []))
  }, [nick, personaId, send])

  useEffect(() => {
    let live = true
    loadSubjects().then((subs) => {
      if (!live) return
      setSubjects(subs)
      setCommunity((cur) => {
        if (cur && subs.some((s) => s.community === cur)) return cur
        return (subs.find((s) => s.profiled_at) || subs[0] || {}).community || null
      })
    }).catch(() => { if (live) setSubjects([]) })
    return () => { live = false }
  }, [loadSubjects, reload])

  useEffect(() => {
    let live = true
    const args = personaId != null ? { persona_id: personaId } : { nick }
    if (community) args.community = community
    send('get_profile', args)
      .then((r) => { if (live) setState({ loading: false, profile: r.ok ? r.profile : null }) })
      .catch(() => { if (live) setState({ loading: false, profile: null }) })
    return () => { live = false }
  }, [nick, personaId, community, reload, send])

  const subject = subjects.find((s) => s.community === community) || null
  const running = subject?.job?.state === 'running'

  // The run takes minutes and lives in the daemon, so the page asks after it
  // rather than waiting on one long reply; leaving and coming back picks the
  // same run up again.
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => {
      send('profile_job', { community: subject.community, kind: subject.kind, key: subject.key })
        .then((r) => {
          if (!r.ok) return
          setSubjects((subs) => subs.map((s) => (s.community === r.community ? { ...s, job: r.job } : s)))
          if (r.job && r.job.state !== 'running') setReload((n) => n + 1)
        })
        .catch(() => {})
    }, POLL_MS)
    return () => clearInterval(t)
  }, [running, subject?.community, subject?.kind, subject?.key, send])

  const run = () => {
    if (!subject) return
    send('build_profile', { community: subject.community, kind: subject.kind, key: subject.key })
      .then((r) => {
        if (r.ok) setSubjects((subs) => subs.map((s) => (s.community === r.community ? { ...s, job: r.job } : s)))
      })
      .catch(() => {})
  }

  const bar = <AnalysisBar subjects={subjects} community={community}
    setCommunity={setCommunity} subject={subject} onRun={run} />

  if (state.loading) return <>{bar}<div className="card"><h2>Profile</h2><div className="empty">Loading…</div></div></>
  if (!state.profile) {
    return (
      <>
        {bar}
        <div className="card">
          <h2>Profile</h2>
          <p className="subtle">
            No profile for this subject yet. Profiles are built by the analysis pass
            over subjects with at least 5 comments.
          </p>
        </div>
      </>
    )
  }

  const p = state.profile
  const lang = p.language || {}
  const metrics = p.metrics || {}
  const gender = p.gender || {}
  const pol = p.politics || {}
  const phil = p.philosophy || {}
  const region = p.region || {}
  const topics = p.topics || {}
  const milieu = p.milieu || {}

  return (
    <>
      {bar}
      <div className="card">
        <h2>Profile — inferred</h2>
        <div className="metrics">
          <Metric k="Probable gender" v={<Gender g={gender} />} />
          <Metric k="Language mastery" v={lang.mastery || '—'} sub={conf(lang.confidence)} />
          <Metric k="Errors / 100 words"
            v={lang.error_rate_per_100_words != null ? lang.error_rate_per_100_words : '—'} />
          <Metric k="Political tendency" v={pol.overall || '—'} sub={conf(pol.confidence)} />
          <Metric k="Region" v={region.guess || 'unknown'} sub={conf(region.confidence)} />
          <Metric k="Register" v={lang.register || '—'} />
        </div>
        <p className="subtle" style={{ marginTop: 12 }}>
          Estimates from the writing, not facts about a person. Blank or “unclear”
          means the comments carried no evidence either way — it is not a middle value.
        </p>
      </div>

      <ReferenceCard r={p.reference} handle={p.label} />

      <div className="card">
        <h2>Language</h2>
        <Axis label="Accents" value={accentText(lang, metrics)} />
        {lang.accent_note && <p className="subtle" style={{ marginTop: 4 }}>{lang.accent_note}</p>}
        {lang.errors && Object.values(lang.errors).some((n) => n > 0) && (
          <>
            <h3 className="sub">Errors by kind</h3>
            <div className="row" style={{ flexWrap: 'wrap', gap: 8 }}>
              {Object.entries(lang.errors).filter(([, n]) => n > 0)
                .sort((a, b) => b[1] - a[1])
                .map(([k, n]) => <span className="chip" key={k}>{k.replace(/_/g, ' ')} · {n}</span>)}
            </div>
          </>
        )}
        {(lang.examples || []).length > 0 && (
          <>
            <h3 className="sub">Examples</h3>
            {lang.examples.map((e, i) => (
              <div className="evidence" key={i}>
                <div className="q">“{e.quote}”</div>
                <div className="subtle">
                  {e.type}{e.issue ? ` — ${e.issue}` : ''}
                  {e.correct ? <> → <em>{e.correct}</em></> : null}
                </div>
              </div>
            ))}
          </>
        )}
        {lang.style_notes && <p style={{ marginTop: 10 }}>{lang.style_notes}</p>}
      </div>

      {(gender.evidence || []).length > 0 && (
        <div className="card">
          <h2>Gender — evidence</h2>
          <p className="subtle">
            Read only from French grammatical self-reference, never from topic or tone.
          </p>
          {gender.evidence.map((e, i) => <div className="evidence" key={i}><div className="q">“{e}”</div></div>)}
        </div>
      )}

      <div className="card">
        <h2>Politics</h2>
        <Scale value={pol.overall} />
        {pol.axes && (
          <div style={{ marginTop: 12 }}>
            {Object.entries(pol.axes).map(([k, v]) => <Axis key={k} label={k} value={v} />)}
          </div>
        )}
        {(pol.periods || []).length > 0 && (
          <>
            <h3 className="sub">Over time — drift: {pol.drift || 'none'}</h3>
            {pol.periods.map((pe, i) => (
              <div className="period" key={i}>
                <span className="when">{pe.from} → {pe.to}</span>
                <span className="lean">{pe.leaning}</span>
                <span className="subtle">{pe.note}</span>
              </div>
            ))}
          </>
        )}
        {(pol.evidence || []).length > 0 && (
          <>
            <h3 className="sub">Evidence</h3>
            {pol.evidence.map((e, i) => <div className="evidence" key={i}><div className="q">“{e}”</div></div>)}
          </>
        )}
      </div>

      {((phil.tendencies || []).length > 0 || phil.religion_signals || (region.markers || []).length > 0
        || (topics.main || []).length > 0) && (
        <div className="card">
          <h2>Philosophy, region and topics</h2>
          {(phil.tendencies || []).length > 0 && (
            <Axis label="Tendencies" value={phil.tendencies.join(', ')} />
          )}
          {phil.religion_signals && <Axis label="Religion signals" value={phil.religion_signals} />}
          {(region.markers || []).length > 0 && (
            <Axis label="Regional markers" value={region.markers.join(', ')} />
          )}
          {(topics.main || []).length > 0 && <Axis label="Main topics" value={topics.main.join(', ')} />}
          {(topics.recurring_targets || []).length > 0 && (
            <Axis label="Recurring targets" value={topics.recurring_targets.join(', ')} />
          )}
        </div>
      )}

      {milieu.summary && (
        <div className="card">
          <h2>Milieu — what the subject volunteers</h2>
          <p>{milieu.summary}</p>
          {known(milieu.origin) && <Axis label="Social origin" value={milieu.origin} />}
          {known(milieu.education) && <Axis label="Education" value={milieu.education} />}
          {known(milieu.occupation) && <Axis label="Occupation" value={milieu.occupation} />}
          {known(milieu.household) && <Axis label="Household" value={milieu.household} />}
          {known(milieu.generation) && <Axis label="Generation" value={milieu.generation} />}
          {(milieu.evidence || []).filter((e) => e.quote || e.reads).map((e, i) => (
            <div className="evidence" key={i}>
              {e.quote && <div className="q">“{e.quote}”</div>}
              {e.reads && <div className="subtle">{e.reads}</div>}
            </div>
          ))}
          <p className="subtle" style={{ marginTop: 12 }}>
            Only what the writer says about themselves, never inferred from their
            opinions. Recorded as stated — trade, office, schooling, origin —
            with one exception: the study keeps no name, of the subject or of
            anyone they mention.
            {milieu.withheld && !/^nothing/i.test(milieu.withheld) &&
              <> <strong>Set aside:</strong> {milieu.withheld}</>}
          </p>
        </div>
      )}

      {/* Distinct from the Notes card: this one is the profiling pass talking,
          and it is rewritten on every run. */}
      {p.notes && <div className="card"><h2>Notes from the analysis</h2><p>{p.notes}</p></div>}
    </>
  )
}

// 'unknown' is the profiling contract's way of saying the dossier was silent;
// showing it as a value would read as a finding.
function known(v) { return v && v !== 'unknown' }

function conf(c) {
  return c == null ? null : `confidence ${Math.round(c * 100)}%`
}

// Judged one comment at a time. A comment with no accent anywhere is a
// keyboard that cannot make them, not a writer who cannot spell — and the same
// person accents properly from another machine an hour later. Only a comment
// that already shows an accent can show a missing one.
function accentText(lang, metrics) {
  const u = lang.accent_usage
  const bare = metrics?.unaccented_comment_share
  const share = bare == null ? null
    : ` — ${Math.round(bare * 100)}% of comments carry no accent at all, which is counted as equipment, not error`
  if (u === 'absent') return 'never typed — input habit, not counted as error'
  if (u === 'full') return `used consistently${share || ''}`
  if (u === 'partial') {
    return `used inconsistently within comments that do carry accents${share || ''}`
  }
  return '—'
}

function Gender({ g }) {
  const male = g.male || 0, female = g.female || 0
  if (g.basis === 'none' || (male < 0.5 && female < 0.5)) {
    return <span className="pending">no evidence</span>
  }
  const [label, v] = male >= female ? ['male', male] : ['female', female]
  return <>{label} <span className="subtle">{Math.round(v * 100)}%</span></>
}

const fmtDate = (iso) => new Date(iso).toLocaleDateString()
const fmtTime = (secs) => new Date(secs * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

// Which community's subject is shown, and the analysis pass for that one
// subject, run now rather than at the next batch.
function AnalysisBar({ subjects, community, setCommunity, subject, onRun }) {
  if (!subjects.length) return null
  const job = subject?.job
  const running = job?.state === 'running'
  const enough = subject && subject.n_comments >= MIN_COMMENTS
  const where = subject ? (COMMUNITIES[subject.community] || subject.community) : ''
  return (
    <div className="card">
      <h2>Analysis pass</h2>
      {subjects.length > 1 && (
        <div className="row" style={{ flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
          {subjects.map((s) => (
            <button key={s.community} className={'chip' + (s.community === community ? ' on' : '')}
              onClick={() => setCommunity(s.community)}>
              {COMMUNITIES[s.community] || s.community} · {s.n_comments} comments
            </button>
          ))}
        </div>
      )}
      {subject && (
        <p className="subtle">
          {subject.kind === 'persona' ? subject.label : `«${subject.label}»`} on {where}:{' '}
          {subject.n_comments} comments.{' '}
          {subject.profiled_at
            ? `Profiled ${fmtDate(subject.profiled_at)} from ${subject.profiled_comments} comments.`
            : 'Not profiled yet.'}
        </p>
      )}
      <div className="row" style={{ flexWrap: 'wrap', gap: 10, alignItems: 'center', marginTop: 10 }}>
        <button className="btn" disabled={!enough || running} onClick={onRun}
          title={subject?.profiled_at ? 'Replaces the stored profile for this community' : undefined}>
          {running ? 'Analysing…' : subject?.profiled_at ? 'Re-run analysis' : 'Run analysis'}
        </button>
        {running && (
          <span className="subtle">
            Started {fmtTime(job.started_at)}. Reading the whole history takes a few
            minutes; you can leave this page.
          </span>
        )}
        {!running && job?.state === 'failed' && <span className="subtle">Failed: {job.error}</span>}
        {!running && job?.state === 'done' && (
          <span className="subtle">
            Done{job.result?.corrections?.length
              ? ` — ${job.result.corrections.length} correction(s) applied on ingest` : ''}.
          </span>
        )}
        {subject && !enough && <span className="subtle">Needs at least {MIN_COMMENTS} comments.</span>}
      </div>
    </div>
  )
}

// Position on the left–right axis, drawn only when the pass committed to one.
function Scale({ value }) {
  const i = LEANINGS.indexOf(value)
  if (i < 0) {
    return <p className="subtle">
      {value === 'mixed'
        ? 'Positions do not sit on a single left–right axis (recorded as mixed).'
        : 'Not enough evidence to place this subject on a left–right axis.'}
    </p>
  }
  return (
    <div className="scale">
      {LEANINGS.map((l, k) => (
        <span key={l} className={'seg' + (k === i ? ' on' : '')} title={l}>
          {k === i ? l : ''}
        </span>
      ))}
    </div>
  )
}

function Axis({ label, value }) {
  if (!value || value === 'unclear') return null
  return (
    <div className="axis">
      <span className="k">{String(label).replace(/_/g, ' ')}</span>
      <span className="v">{value}</span>
    </div>
  )
}

function Metric({ k, v, sub }) {
  return (
    <div className="metric">
      <div className="v">{v}</div>
      <div className="k">{k}</div>
      {sub && <div className="k" style={{ textTransform: 'none', opacity: .8 }}>{sub}</div>}
    </div>
  )
}
