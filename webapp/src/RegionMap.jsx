import { useMemo } from 'react'

// Region is a place, so it gets a map rather than a bar chart — but a rough
// one. The tiles are laid out in roughly the geography of Romandie (Jura and
// Bern north, Geneva and Valais south) without pretending to be canton shapes,
// because the underlying guess is itself coarse.
//
// The honest part is underneath: most subjects cannot be placed in a canton at
// all, and those are shown as tiles too rather than dropped off the picture.

const TILES = [
  { id: 'Jura',      col: 0, row: 0 },
  { id: 'Bern',      col: 2, row: 0 },
  { id: 'Neuchâtel', col: 0, row: 1 },
  { id: 'Fribourg',  col: 2, row: 1 },
  { id: 'Vaud',      col: 1, row: 2 },
  { id: 'Geneva',    col: 0, row: 3 },
  { id: 'Valais',    col: 2, row: 3 },
]

const OFF_MAP = ['Romandie-unspecified', 'unknown', 'France', 'other']

export default function RegionMap({ subjects }) {
  const counts = useMemo(() => {
    const m = new Map()
    for (const s of subjects) m.set(s.region || 'unknown', (m.get(s.region || 'unknown') || 0) + 1)
    return m
  }, [subjects])

  const placed = TILES.map((t) => ({ ...t, n: counts.get(t.id) || 0 }))
  const peak = Math.max(1, ...placed.map((t) => t.n))
  const off = OFF_MAP.map((k) => ({ id: k, n: counts.get(k) || 0 })).filter((o) => o.n)
  const total = subjects.length

  return (
    <div className="card">
      <h2>Linguistic region</h2>
      <div className="cantonmap">
        {placed.map((t) => (
          <div key={t.id} className={'canton' + (t.n ? '' : ' empty')}
            style={{ gridColumn: t.col + 1, gridRow: t.row + 1,
                     '--fill': t.n ? (0.12 + 0.88 * (t.n / peak)).toFixed(3) : 0 }}
            title={`${t.id}: ${t.n} subject${t.n === 1 ? '' : 's'}`}>
            <span className="nm">{t.id}</span>
            <span className="ct">{t.n || ''}</span>
          </div>
        ))}
      </div>

      <div className="row" style={{ gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
        {off.map((o) => (
          <span className="chip" key={o.id}>{o.id} · {o.n}</span>
        ))}
      </div>

      <p className="subtle" style={{ marginTop: 10 }}>
        From helvetisms (<em>septante</em>, <em>natel</em>, <em>panosse</em>) and local
        knowledge. Only {placed.reduce((a, t) => a + t.n, 0)} of {total} subjects
        say enough to place in a canton — Romandie-unspecified is the honest
        answer for most, and is not a guess at Vaud.
      </p>
    </div>
  )
}
