import { useCallback, useEffect } from 'react'

// Full-screen view of one picture, with its context.
//
// The context is the point. A photograph on its own is decoration; the same
// photograph with its caption, its credit, the headline it ran under and the
// date it ran is a record of an editorial decision, which is the thing this
// project is actually about. So the caption is never truncated here, however
// long it runs, and the article it belongs to is one click away.
//
// It shows the 1200px downscale rather than the original. Originals average
// 1.7k pixels wide and a few hundred kilobytes; on a screen this is
// indistinguishable and roughly ten times lighter. The original stays one
// click away for anyone who wants to look closely.

export default function Gallery({ items, index, onClose, onMove }) {
  const item = items[index]

  const key = useCallback((e) => {
    if (e.key === 'Escape') onClose()
    else if (e.key === 'ArrowLeft') onMove(-1)
    else if (e.key === 'ArrowRight') onMove(1)
  }, [onClose, onMove])

  useEffect(() => {
    window.addEventListener('keydown', key)
    // Stop the page behind from scrolling while the overlay owns the screen.
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', key)
      document.body.style.overflow = prev
    }
  }, [key])

  if (!item) return null
  const x = item.extra || {}
  const dims = x.width && x.height ? `${x.width}×${x.height}` : null
  const weight = x.bytes ? `${Math.round(x.bytes / 1024)} KB` : null

  return (
    <div className="gal-backdrop" onClick={onClose} role="dialog" aria-modal="true"
         aria-label="Picture viewer">
      <button className="gal-close" onClick={onClose} aria-label="Close">✕</button>
      {index > 0 && (
        <button className="gal-nav left"
                onClick={(e) => { e.stopPropagation(); onMove(-1) }}
                aria-label="Previous picture">‹</button>
      )}
      {index < items.length - 1 && (
        <button className="gal-nav right"
                onClick={(e) => { e.stopPropagation(); onMove(1) }}
                aria-label="Next picture">›</button>
      )}

      <figure className="gal-figure" onClick={(e) => e.stopPropagation()}>
        <img src={`/thumb/m/${item.ref}`} alt={item.title || ''} />
        <figcaption>
          {item.title && <div className="gal-caption">{item.title}</div>}
          <div className="gal-meta subtle">
            {[item.journal, item.when, x.credit, dims, weight]
              .filter(Boolean).join(' · ')}
          </div>
          {x.headline && <div className="gal-head">Ran under: {x.headline}</div>}
          <div className="gal-links">
            <a href={`/blob/${item.ref}`} target="_blank" rel="noreferrer">
              original file
            </a>
            {x.url && (
              <a href={x.url} target="_blank" rel="noreferrer">published page</a>
            )}
          </div>
          <div className="gal-count subtle">{index + 1} of {items.length}</div>
        </figcaption>
      </figure>
    </div>
  )
}
