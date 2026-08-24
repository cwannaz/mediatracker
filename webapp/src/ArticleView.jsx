import { useEffect, useState } from 'react'

const fmt = (v) => {
  if (!v) return '—'
  try { return new Date(v).toLocaleString() } catch { return String(v) }
}

// Rewrite the archived <img src> to the local blob store so an article renders
// offline, and drop any <script> that came from the scraped markup.
function localize(html, images) {
  if (!html) return ''
  let out = html.replace(/<script[\s\S]*?<\/script>/gi, '')
  for (const img of images || []) {
    if (!img.orig_url) continue
    out = out.split(img.orig_url).join(`/blob/${img.sha256}`)
  }
  return out
}

export default function ArticleView({ articleId, send, onBack, onNick }) {
  const [art, setArt] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    let alive = true
    send('get_article', { article_id: articleId })
      .then((r) => { if (!alive) return; r.ok ? setArt(r.article) : setErr(r.error) })
      .catch((e) => alive && setErr(String(e)))
    return () => { alive = false }
  }, [articleId, send])

  if (err) return <><button className="backlink" onClick={onBack}>← Back</button><div className="empty">{err}</div></>
  if (!art) return <><button className="backlink" onClick={onBack}>← Back</button><div className="empty">Loading…</div></>

  const byId = new Map((art.comments || []).map((c) => [c.id, c]))
  const roots = (art.comments || []).filter((c) => !c.parent_id || !byId.has(c.parent_id))
  const kids = new Map()
  for (const c of art.comments || []) {
    if (c.parent_id && byId.has(c.parent_id)) {
      if (!kids.has(c.parent_id)) kids.set(c.parent_id, [])
      kids.get(c.parent_id).push(c)
    }
  }

  const hero = (art.images || []).find((i) => i.role === 'hero')
  const bodyHtml = localize(art.body_html, art.images)

  return (
    <>
      <button className="backlink" onClick={onBack}>← Back to articles</button>
      <article className="article">
        <h1>{art.headline || '(untitled)'}</h1>
        {art.subhead && <p className="lead">{art.subhead}</p>}
        <div className="byline">
          {art.journal_name}
          {art.section && ` · ${art.section}`}
          {art.author && ` · ${art.author}`}
          {art.source && ` · ${art.source}`}
          {` · ${fmt(art.published_at)}`}
          {art.origin === 'pdf' && <span className="chip pdf">from PDF archive</span>}
        </div>

        {!bodyHtml && hero && (
          <figure><img src={`/blob/${hero.sha256}`} alt={hero.alt_text || ''} />
            {hero.caption && <figcaption>{hero.caption}</figcaption>}</figure>
        )}

        {bodyHtml
          ? <div className="body" dangerouslySetInnerHTML={{ __html: bodyHtml }} />
          : art.body_text
            ? <div className="body">{art.body_text.split('\n').map((p, i) => <p key={i}>{p}</p>)}</div>
            : <p className="pending">No article body in this capture (comments-only archive page).</p>}

        <h2 style={{ marginTop: 28 }}>{(art.comments || []).length} comments</h2>
        {(art.comments || []).length === 0 && <div className="empty">No comments recorded.</div>}
        {roots.map((c) => (
          <Comment key={c.id} c={c} kids={kids} onNick={onNick} />
        ))}
        {art.canonical_url?.startsWith('http') && (
          <p className="subtle" style={{ marginTop: 24 }}>
            Source: <a href={art.canonical_url} target="_blank" rel="noreferrer">{art.canonical_url}</a>
            {art.source_file && ` · archived from ${art.source_file}`}
          </p>
        )}
      </article>
    </>
  )
}

function Comment({ c, kids, onNick, depth = 0 }) {
  const votes = c.like_count
  const reactions = c.raw_meta?.reactions
  const label = reactions && Object.keys(reactions).length
    ? Object.entries(reactions).map(([k, v]) => `${k} ${v}`).join(' · ')
    : null
  return (
    <>
      <div className={depth ? 'comment reply' : 'comment'}>
        <div className="head">
          <span className="nick" onClick={() => onNick?.(c.author_nick)}>{c.author_nick || '(anonymous)'}</span>
          <span className="when">{c.posted_at ? fmt(c.posted_at) : 'date unknown'}</span>
          <span className="votes">{label || (votes != null ? `${votes} votes` : '')}</span>
        </div>
        {c.body_text
          ? <div className="text">{c.body_text}</div>
          : <div className="text missing">[body not recoverable from this capture]</div>}
      </div>
      {(kids.get(c.id) || []).map((k) => (
        <Comment key={k.id} c={k} kids={kids} onNick={onNick} depth={depth + 1} />
      ))}
    </>
  )
}
