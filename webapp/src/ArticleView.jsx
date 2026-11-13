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
  const [busy, setBusy] = useState(false)
  const [again, setAgain] = useState(null)

  const load = (quiet = false) => {
    if (!quiet) setArt(null)
    return send('get_article', { article_id: articleId })
      .then((r) => { r.ok ? setArt(r.article) : setErr(r.error) })
      .catch((e) => setErr(String(e)))
  }

  useEffect(() => {
    let alive = true
    setAgain(null)
    send('get_article', { article_id: articleId })
      .then((r) => { if (!alive) return; r.ok ? setArt(r.article) : setErr(r.error) })
      .catch((e) => alive && setErr(String(e)))
    return () => { alive = false }
  }, [articleId, send])

  // Read the article at the title again, now. A thread keeps moving after we
  // stop watching it -- votes above all, which change without the comment
  // count changing -- so this is the only way to bring one up to date without
  // waiting for the crawl to come back round to it.
  const reabsorb = () => {
    setBusy(true); setAgain(null)
    send('reabsorb_article', { article_id: articleId })
      .then((r) => {
        setAgain(r.ok ? r : { error: r.error })
        if (r.ok) return load(true)      // keep the page in place while it refreshes
      })
      .catch((e) => setAgain({ error: String(e) }))
      .finally(() => setBusy(false))
  }

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
        <div className="toolbar">
          {art.canonical_url?.startsWith('http') && (
            <a className="btn secondary" href={art.canonical_url}
               target="_blank" rel="noreferrer noopener"
               title={art.canonical_url}>Open at {art.journal_name || 'the source'} ↗</a>
          )}
          <button className="btn" onClick={reabsorb} disabled={busy}
                  title="Fetch the article and its whole thread again, and record what changed">
            {busy ? 'Re-absorbing…' : 'Re-absorb'}
          </button>
          {again && (
            <span className="subtle">
              {again.error
                ? again.error
                : again.gone
                  ? 'the title no longer serves this article; marked gone'
                  : `${again.comment_snapshots} comment snapshot${again.comment_snapshots === 1 ? '' : 's'}, `
                    + `${again.article_snapshots} article snapshot${again.article_snapshots === 1 ? '' : 's'}`
                    + (again.images_new ? `, ${again.images_new} new image${again.images_new === 1 ? '' : 's'}` : '')
                    + (again.comment_snapshots || again.article_snapshots ? '' : ' — nothing had changed')}
            </span>
          )}
        </div>
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
