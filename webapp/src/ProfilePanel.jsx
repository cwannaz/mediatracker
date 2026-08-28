import { useEffect, useState } from 'react'
import { ReferenceCard } from './Reference.jsx'

// The inferred half of a subject's profile: gender, language mastery, politics
// (with drift), philosophy, region and milieu. Everything here is an ESTIMATE produced
// by the profiling pass, so each block shows its confidence and the verbatim
// quotes it rests on. Deterministic style measures live in their own card and
// are deliberately not mixed in.

const LEANINGS = ['far-left', 'left', 'centre-left', 'centre',
                  'centre-right', 'right', 'far-right']

export default function ProfilePanel({ nick, personaId, send }) {
  const [state, setState] = useState({ loading: true, profile: null })

  useEffect(() => {
    let live = true
    const args = personaId != null ? { persona_id: personaId } : { nick }
    send('get_profile', args)
      .then((r) => { if (live) setState({ loading: false, profile: r.ok ? r.profile : null }) })
      .catch(() => { if (live) setState({ loading: false, profile: null }) })
    return () => { live = false }
  }, [nick, personaId, send])

  if (state.loading) return <div className="card"><h2>Profile</h2><div className="empty">Loading…</div></div>
  if (!state.profile) {
    return (
      <div className="card">
        <h2>Profile</h2>
        <p className="subtle">
          No profile for this subject yet. Profiles are built by the analysis pass
          over subjects with at least 5 comments.
        </p>
      </div>
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

      {p.notes && <div className="card"><h2>Notes</h2><p>{p.notes}</p></div>}
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
