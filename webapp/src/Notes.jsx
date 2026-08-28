import { useCallback, useEffect, useState } from 'react'

// What a reader knows about a commenter that the corpus cannot hold.
//
// Everything else on a subject's page is derived from the stored comments and
// would be rebuilt identically by the next pass. A note is the opposite: an
// observation someone made — a handle recognised on another platform, a rename
// that happened where we do not collect, a reading that is wrong — and it has
// to survive every re-run of the machine, which is why it lives in its own
// table rather than in the profile row.
//
// The subject is addressed the way profiles are: a persona by id, otherwise a
// nickname. A persona shows the notes written against its handles too, each
// labelled with the handle, so linking two nicknames never buries what was
// already recorded about either.

const fmt = (v) => { try { return new Date(v).toLocaleDateString() } catch { return String(v) } }
const isUrl = (s) => /^https?:\/\//i.test(s || '')

// A bare URL is unreadable in a list; the host is the part that says what kind
// of source this is, which is what a reader is scanning for.
const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, '') } catch { return u } }

export default function Notes({ nick, personaId, send }) {
  const [notes, setNotes] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [editing, setEditing] = useState(null)   // note id, or 'new'

  const subject = personaId != null ? { persona_id: personaId } : { nick }
  const key = personaId != null ? `p${personaId}` : `n${nick}`

  const call = useCallback((cmd, args) => {
    setBusy(true); setErr(null)
    return send(cmd, { ...(personaId != null ? { persona_id: personaId } : { nick }), ...args })
      .then((r) => {
        if (r.ok) { setNotes(r.notes || []); setEditing(null) } else setErr(r.error)
        return r
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setBusy(false))
  }, [nick, personaId, send])

  useEffect(() => { call('list_notes', {}) }, [key]) // eslint-disable-line

  const mine = personaId != null ? 'persona' : 'nick'

  return (
    <div className="card">
      <h2>Notes</h2>

      {notes === null && <div className="empty">Loading…</div>}
      {notes !== null && notes.length === 0 && editing !== 'new' && (
        <p className="subtle">
          Nothing recorded by hand. Notes are for what the comments cannot say —
          a handle seen on another platform, a rename that happened elsewhere, a
          correction to something the analysis got wrong.
        </p>
      )}

      {(notes || []).map((n) => (
        editing === n.id ? (
          <NoteForm key={n.id} note={n} busy={busy}
            onCancel={() => setEditing(null)}
            onSave={(body, source) => call('update_note', { note_id: n.id, body, source })} />
        ) : (
          <div className="note" key={n.id}>
            <div className="notebody">{n.body}</div>
            <div className="notemeta">
              <span>{fmt(n.created_at)}</span>
              {n.updated_at && n.updated_at !== n.created_at &&
                <span>edited {fmt(n.updated_at)}</span>}
              {/* A note written before the persona existed still belongs to it,
                  but a reader should be able to see which handle it was about. */}
              {n.subject_kind !== mine && n.subject_kind === 'nick' &&
                <span className="chip">written on {n.subject_key}</span>}
              {n.source && (isUrl(n.source)
                ? <a href={n.source} target="_blank" rel="noreferrer noopener"
                    title={n.source}>{host(n.source)}</a>
                : <span>{n.source}</span>)}
              <span className="spacer" />
              <button className="iconbtn" disabled={busy}
                onClick={() => setEditing(n.id)}>edit</button>
              <button className="iconbtn" disabled={busy}
                onClick={() => call('delete_note', { note_id: n.id })}>delete</button>
            </div>
          </div>
        )
      ))}

      {err && <p className="subtle" style={{ color: 'var(--down)' }}>{err}</p>}

      {editing === 'new'
        ? <NoteForm busy={busy} onCancel={() => setEditing(null)}
            onSave={(body, source) => call('add_note', { body, source })} />
        : <div className="actions">
            <button className="btn secondary" disabled={busy}
              onClick={() => setEditing('new')}>Add a note</button>
          </div>}
    </div>
  )
}

function NoteForm({ note, busy, onSave, onCancel }) {
  const [body, setBody] = useState(note?.body || '')
  const [source, setSource] = useState(note?.source || '')
  const can = body.trim().length > 0 && !busy

  return (
    <div className="noteform">
      <textarea rows={4} value={body} autoFocus
        placeholder="What you know and how you know it."
        onChange={(e) => setBody(e.target.value)} />
      <input type="text" value={source}
        placeholder="Source — a URL, a thread, where you saw it (optional)"
        onChange={(e) => setSource(e.target.value)} />
      <div className="actions">
        <button className="btn" disabled={!can}
          onClick={() => onSave(body, source)}>{note ? 'Save' : 'Add note'}</button>
        <button className="btn secondary" disabled={busy} onClick={onCancel}>Cancel</button>
        <span className="subtle" style={{ alignSelf: 'center' }}>
          Names stay out: the study records what places a writer, never who they are.
        </span>
      </div>
    </div>
  )
}
