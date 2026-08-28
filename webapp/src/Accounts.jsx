import { useCallback, useEffect, useState } from 'react'

// The same writer on networks this study does not collect.
//
// Kept structured rather than written into a note, because the questions worth
// asking about it are counting ones: how many of a paper's commenters carry an
// identity on another network, which network, and whether a rename there lines
// up with a rename here. A paragraph of prose answers none of those.
//
// A link is enough on its own — the platform is read off the host rather than
// asked for, so the two can never disagree.

const CONFIDENCE = ['confirmed', 'probable', 'possible']

const fmt = (v) => { try { return new Date(v).toLocaleDateString() } catch { return String(v) } }
const short = (u) => {
  try {
    const p = new URL(u)
    return p.hostname.replace(/^www\./, '') + p.pathname.replace(/\/$/, '')
  } catch { return u }
}

export default function Accounts({ nick, personaId, send }) {
  const [rows, setRows] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [editing, setEditing] = useState(null)   // account id, or 'new'

  const key = personaId != null ? `p${personaId}` : `n${nick}`

  const call = useCallback((cmd, args) => {
    setBusy(true); setErr(null)
    return send(cmd, { ...(personaId != null ? { persona_id: personaId } : { nick }), ...args })
      .then((r) => {
        if (r.ok) { setRows(r.accounts || []); setEditing(null) } else setErr(r.error)
        return r
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setBusy(false))
  }, [nick, personaId, send])

  useEffect(() => { call('list_accounts', {}) }, [key]) // eslint-disable-line

  const mine = personaId != null ? 'persona' : 'nick'

  return (
    <div className="card">
      <h2>Elsewhere</h2>

      {rows === null && <div className="empty">Loading…</div>}
      {rows !== null && rows.length === 0 && editing !== 'new' && (
        <p className="subtle">
          No account recorded on another network. This is where the same writer
          goes when they are recognised somewhere we do not collect — a paper's
          Facebook page, a blog, a handle on another platform.
        </p>
      )}

      {(rows || []).map((a) => (
        editing === a.id ? (
          <AccountForm key={a.id} account={a} busy={busy}
            onCancel={() => setEditing(null)}
            onSave={(f) => call('update_account', { account_id: a.id, ...f })} />
        ) : (
          <div className="acct" key={a.id}>
            <div className="acctline">
              <span className={'platform p-' + a.platform}>{a.platform}</span>
              {a.handle && <strong>{a.handle}</strong>}
              {a.url && <a href={a.url} target="_blank" rel="noreferrer noopener"
                title={a.url}>{short(a.url)}</a>}
              <span className={'chip' + (a.confidence === 'confirmed' ? '' : ' pdf')}>
                {a.confidence}
              </span>
              <span className="spacer" />
              <button className="iconbtn" disabled={busy}
                onClick={() => setEditing(a.id)}>edit</button>
              <button className="iconbtn" disabled={busy}
                onClick={() => call('delete_account', { account_id: a.id })}>remove</button>
            </div>
            <div className="notemeta">
              {a.evidence && <span className="acctwhy">{a.evidence}</span>}
              {/* Recorded against a handle before the persona existed. */}
              {a.subject_kind !== mine && a.subject_kind === 'nick' &&
                <span className="chip">on {a.subject_key}</span>}
              <span>added {fmt(a.added_at)}</span>
            </div>
          </div>
        )
      ))}

      {err && <p className="subtle" style={{ color: 'var(--down)' }}>{err}</p>}

      {editing === 'new'
        ? <AccountForm busy={busy} onCancel={() => setEditing(null)}
            onSave={(f) => call('add_account', f)} />
        : <div className="actions">
            <button className="btn secondary" disabled={busy}
              onClick={() => setEditing('new')}>Add an account</button>
          </div>}
    </div>
  )
}

function AccountForm({ account, busy, onSave, onCancel }) {
  const [url, setUrl] = useState(account?.url || '')
  const [handle, setHandle] = useState(account?.handle || '')
  const [confidence, setConfidence] = useState(account?.confidence || 'confirmed')
  const [evidence, setEvidence] = useState(account?.evidence || '')
  const can = (url.trim() || handle.trim()) && !busy

  return (
    <div className="noteform">
      <input type="text" value={url} autoFocus
        placeholder="Link to the account — the platform is read from it"
        onChange={(e) => setUrl(e.target.value)} />
      <input type="text" value={handle}
        placeholder="Name the account goes by there (optional)"
        onChange={(e) => setHandle(e.target.value)} />
      <textarea rows={2} value={evidence}
        placeholder="How you know it is the same writer (optional)"
        onChange={(e) => setEvidence(e.target.value)} />
      <div className="actions">
        <select value={confidence} onChange={(e) => setConfidence(e.target.value)}>
          {CONFIDENCE.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <button className="btn" disabled={!can}
          onClick={() => onSave({ url, handle, confidence, evidence })}>
          {account ? 'Save' : 'Add account'}
        </button>
        <button className="btn secondary" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}
