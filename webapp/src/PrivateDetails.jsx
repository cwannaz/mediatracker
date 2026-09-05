import { useEffect, useState } from 'react'

// What a commenter said about their own circumstances, in their own words.
//
// Deliberately its own card rather than a block inside ProfilePanel. That
// panel is the LLM's reading of a subject and every figure in it carries a
// confidence, because every figure in it is a guess. Nothing here is a guess:
// each line is a sentence the person wrote, matched by pattern and shown
// verbatim. Mixing the two would invite the estimates to borrow the standing
// of the quotes.
//
// No summary, no bracket, no inferred class. The socio-economic reading is the
// reader's to make from the sentences, which is also the honest way round: a
// label like "working class" derived from one mention of a CFC would be a
// claim the corpus cannot support, while the sentence supports itself.

export default function PrivateDetails({ nick, personaId, send }) {
  const [state, setState] = useState({ loading: true, data: null })
  const [open, setOpen] = useState({})

  useEffect(() => {
    let live = true
    const args = personaId != null ? { persona_id: personaId } : { nick }
    send('profile_disclosures', args)
      .then((r) => { if (live) setState({ loading: false, data: r.ok ? r : null }) })
      .catch(() => { if (live) setState({ loading: false, data: null }) })
    return () => { live = false }
  }, [nick, personaId, send])

  if (state.loading) {
    return <div className="card"><h2>Private details</h2><div className="empty">Loading…</div></div>
  }
  const d = state.data
  if (!d || !d.groups || d.groups.length === 0) {
    return (
      <div className="card">
        <h2>Private details</h2>
        <p className="subtle">
          Nothing stated outright. Most commenters never describe their own
          circumstances — roughly one in twenty does — so silence here is the
          usual case and says nothing about the person.
        </p>
      </div>
    )
  }

  return (
    <div className="card">
      <div className="card-head">
        <h2>Private details</h2>
        <span className="subtle">
          {d.n_disclosures} in {d.n_comments} comments · {d.per_1000}/1000 words
        </span>
      </div>
      <p className="subtle tl-note">
        Sentences this person wrote about themselves, quoted as written and
        never summarised. A count is not a claim about how open someone is:
        writing more produces more of these, which is what the rate is for.
      </p>

      {d.groups.map((g) => {
        const shown = open[g.key] ? g.quotes : g.quotes.slice(0, 2)
        return (
          <div className="pd-group" key={g.key}>
            <div className="pd-head">
              <span className="pd-label">{g.label}</span>
              <span className="pd-count">{g.n}</span>
            </div>
            {shown.map((q, i) => (
              <div className="evidence" key={i}>
                <div className="q">“{q.quote}”</div>
                <div className="pd-when subtle">
                  {[q.when, q.journal].filter(Boolean).join(' · ')}
                </div>
              </div>
            ))}
            {g.quotes.length > 2 && (
              <button className="linkish"
                      onClick={() => setOpen((o) => ({ ...o, [g.key]: !o[g.key] }))}>
                {open[g.key]
                  ? 'show fewer'
                  : `show ${g.quotes.length - 2} more`}
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}
