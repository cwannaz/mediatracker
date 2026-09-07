import { useState } from 'react'
import Gallery from './Gallery.jsx'

// A contact sheet of the picture store.
//
// Cells are a fixed height with the image covering them, rather than a masonry
// layout that reflows as pictures load. A grid that settles while you are
// reading it is worse than one that crops, and cropping is recoverable — the
// gallery shows the whole frame.
//
// Thumbnails are made on demand by the blob route, so the first pass over an
// unseen stretch of the archive is slower than the second. `loading="lazy"`
// keeps that cost to what is actually on screen, which matters when the
// filter matches forty thousand pictures.

export default function ImageGrid({ rows, onNeedMore, more }) {
  const [open, setOpen] = useState(-1)

  if (!rows.length) return null

  return (
    <>
      <div className="imgrid">
        {rows.map((r, i) => (
          <button key={r.ref} className="imcell" onClick={() => setOpen(i)}
                  title={r.title || ''}>
            <img src={`/thumb/t/${r.ref}`} alt={r.title || ''} loading="lazy" />
            <span className="imcap">
              {r.title || <em className="subtle">no caption</em>}
            </span>
            <span className="imwhen subtle">
              {[r.journal, r.when].filter(Boolean).join(' · ')}
            </span>
          </button>
        ))}
      </div>
      {more && (
        <div className="more-row">
          <button className="linkish" onClick={onNeedMore}>load more pictures</button>
        </div>
      )}
      {open >= 0 && (
        <Gallery
          items={rows}
          index={open}
          onClose={() => setOpen(-1)}
          onMove={(d) => setOpen((n) => Math.min(rows.length - 1, Math.max(0, n + d)))}
        />
      )}
    </>
  )
}
