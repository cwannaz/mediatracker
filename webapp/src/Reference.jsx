import { useCallback, useState } from 'react'

// What a handle is borrowed from, in a table cell and in full.
//
// The bubble is positioned fixed rather than absolutely, because the cell it
// hangs off lives inside a horizontally scrolling table: an absolutely
// positioned popover would be clipped by that container exactly when the
// column is far enough right to need one.

const DOMAIN_LABEL = {
  politics: 'politics', history: 'history', literature: 'literature',
  cinema: 'cinema', television: 'television', music: 'music', comics: 'comics',
  sport: 'sport', mythology: 'mythology', religion: 'religion',
  science: 'science', nature: 'nature', brand: 'brand',
  geography: 'geography', language: 'language', internet: 'internet',
}

const DEVICE_TEXT = {
  borrowed: 'taken as it stands',
  pun: 'wordplay — the reader is expected to complete it',
  blend: 'two words run together',
  altered: 'deliberately misspelt',
  combined: 'two references welded into one',
}

// Blank is the honest majority state: the lexicon is hand-checked and reaches
// a small fraction of the handles, so an empty cell means "not recognised",
// never "no reference".
export default function Reference({ r }) {
  const [at, setAt] = useState(null)

  const show = useCallback((e) => {
    const b = e.currentTarget.getBoundingClientRect()
    setAt({ x: Math.min(b.left, window.innerWidth - 330), y: b.bottom + 6 })
  }, [])
  const hide = useCallback(() => setAt(null), [])

  if (!r) return <span className="subtle">—</span>
  return (
    <>
      <span className="ref" tabIndex={0}
        onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide}>
        <span className={'refdomain d-' + r.domain}>{DOMAIN_LABEL[r.domain] || r.domain}</span>
        <span className="refwhat">{r.refers_to}</span>
        {r.device !== 'borrowed' && <em className="refdevice">{r.device}</em>}
        {r.confidence !== 'high' && <em className="refdevice" title="uncertain reading">?</em>}
      </span>
      {at && (
        <span className="refbubble" style={{ left: at.x, top: at.y }} role="tooltip">
          <strong>{r.refers_to}</strong>
          <span className="bdomain">{DOMAIN_LABEL[r.domain] || r.domain}</span>
          {r.note && <span className="bnote">{r.note}</span>}
          <span className="bmeta">{DEVICE_TEXT[r.device] || r.device}</span>
          {r.via && <span className="bmeta">read from the handle {r.via}</span>}
          {r.matched === 'stem' && (
            <span className="bmeta">matched without its trailing number</span>
          )}
          {r.confidence !== 'high' && (
            <span className="bwarn">{r.confidence} confidence — this reading may be wrong</span>
          )}
        </span>
      )}
    </>
  )
}

// The same reading given room, for a subject's own page.
export function ReferenceCard({ r, handle }) {
  if (!r) return null
  return (
    <div className="card">
      <h2>Nickname — a reference</h2>
      <p className="refhead">
        <span className="refhandle">{r.via || handle}</span>
        <span className="refarrow">→</span>
        <span className="reftarget">{r.refers_to}</span>
        <span className={'refdomain d-' + r.domain}>{DOMAIN_LABEL[r.domain] || r.domain}</span>
      </p>
      {r.note && <p style={{ margin: '0 0 10px' }}>{r.note}</p>}
      <div className="metrics">
        <Small k="Kind of culture" v={DOMAIN_LABEL[r.domain] || r.domain} />
        <Small k="How it is used" v={DEVICE_TEXT[r.device] || r.device} />
        <Small k="Confidence" v={r.confidence} />
        <Small k="Matched" v={r.matched === 'stem' ? 'ignoring a trailing number' : 'exactly'} />
      </div>
      <p className="subtle" style={{ marginTop: 12 }}>
        A pseudonym is a choice, and the choice places a writer — the generation,
        the schooling, whether the frame is French, Swiss, anglophone or classical.
        This describes the allusion the commenter published, and nothing about who
        they are: it records that the handle points at {r.refers_to}, not that the
        writer has any connection to it. Readings are hand-checked and meant to be
        argued with.
      </p>
    </div>
  )
}

function Small({ k, v }) {
  return <div className="metric"><div className="v" style={{ fontSize: 13 }}>{v}</div>
    <div className="k">{k}</div></div>
}
