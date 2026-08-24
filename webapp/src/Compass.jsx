import { useMemo, useState } from 'react'

// Where the commenting public sits, as a cloud rather than a set of bars.
//
// X is the left–right axis; Y is command of the language. Those are the two
// scales the pass produces that are genuinely ordered, so they are the two
// that can carry a position. Dot area is comment volume.
//
// Subjects the pass would not place — 'mixed' (positions that do not sit on a
// single axis) and 'unclear' (no usable evidence) — are deliberately NOT
// plotted at the centre. Putting them there would invent a moderate reading
// for people who simply never showed one. They are counted underneath instead.

const X = ['far-left', 'left', 'centre-left', 'centre', 'centre-right', 'right', 'far-right']
const Y = ['native-fluent', 'fluent', 'good', 'approximate', 'poor']

const W = 760, H = 340, PAD_L = 96, PAD_B = 42, PAD_T = 16, PAD_R = 16

export default function Compass({ subjects, onOpen }) {
  const [hover, setHover] = useState(null)

  const { dots, unplaced, maxN } = useMemo(() => {
    const placed = [], out = []
    for (const s of subjects) {
      const xi = X.indexOf(s.leaning), yi = Y.indexOf(s.mastery)
      if (xi < 0 || yi < 0) { out.push(s); continue }
      placed.push({ s, xi, yi })
    }
    // Several subjects land on the same cell, so spread them inside it with a
    // stable offset — same input, same picture every render.
    const cells = new Map()
    for (const p of placed) {
      const k = `${p.xi}:${p.yi}`
      const idx = cells.get(k) || 0
      cells.set(k, idx + 1)
      p.slot = idx
    }
    for (const p of placed) {
      const total = cells.get(`${p.xi}:${p.yi}`)
      const ring = Math.floor(Math.sqrt(p.slot))
      const around = p.slot - ring * ring
      const ang = (around / Math.max(1, 2 * ring + 1)) * Math.PI * 2 + ring
      p.jx = total > 1 ? Math.cos(ang) * ring * 7.5 : 0
      p.jy = total > 1 ? Math.sin(ang) * ring * 7.5 : 0
    }
    return {
      dots: placed,
      unplaced: out,
      maxN: Math.max(1, ...placed.map((p) => p.s.n_comments || 1)),
    }
  }, [subjects])

  const px = (xi) => PAD_L + (xi + 0.5) * ((W - PAD_L - PAD_R) / X.length)
  const py = (yi) => PAD_T + (yi + 0.5) * ((H - PAD_T - PAD_B) / Y.length)
  const r = (n) => 3 + 13 * Math.sqrt((n || 1) / maxN)

  const colour = (s) => (s.male >= 0.6 ? 'm' : s.female >= 0.6 ? 'f' : 'u')

  return (
    <div className="card">
      <h2>The commenting public</h2>
      <div className="figwrap">
        <svg viewBox={`0 0 ${W} ${H}`} className="compass" role="img"
          aria-label="Scatter of subjects by political leaning and language mastery">
          {Y.map((m, i) => (
            <g key={m}>
              <line x1={PAD_L} x2={W - PAD_R} y1={py(i)} y2={py(i)} className="grid" />
              <text x={PAD_L - 10} y={py(i)} className="axlab" textAnchor="end"
                dominantBaseline="middle">{m}</text>
            </g>
          ))}
          {X.map((l, i) => (
            <text key={l} x={px(i)} y={H - PAD_B + 18} className="axlab" textAnchor="middle">
              {l.replace('centre-', 'c-').replace('far-', 'far ')}
            </text>
          ))}
          <line x1={px(3)} x2={px(3)} y1={PAD_T} y2={H - PAD_B} className="grid mid" />

          {dots.map((p) => (
            <circle key={`${p.s.subject_kind}:${p.s.subject_key}`}
              cx={px(p.xi) + p.jx} cy={py(p.yi) + p.jy} r={r(p.s.n_comments)}
              className={`dot g-${colour(p.s)}${hover === p.s ? ' on' : ''}`}
              onMouseEnter={() => setHover(p.s)} onMouseLeave={() => setHover(null)}
              onClick={() => onOpen?.(p.s)}>
              <title>{p.s.label} — {p.s.leaning}, {p.s.mastery}, {p.s.n_comments} comments</title>
            </circle>
          ))}

          <text x={(W + PAD_L) / 2} y={H - 4} className="axtitle" textAnchor="middle">
            political leaning
          </text>
          <text x={-(PAD_T + (H - PAD_B - PAD_T) / 2)} y={12} className="axtitle"
            textAnchor="middle" transform="rotate(-90)">
            language mastery
          </text>
        </svg>
      </div>

      <div className="row" style={{ gap: 18, flexWrap: 'wrap', marginTop: 6 }}>
        <span className="key"><i className="dot g-m" /> male</span>
        <span className="key"><i className="dot g-f" /> female</span>
        <span className="key"><i className="dot g-u" /> no gender evidence</span>
        <span className="subtle">dot size = comments written</span>
      </div>

      <p className="subtle" style={{ marginTop: 10 }}>
        {hover
          ? <><strong>{hover.label}</strong> — {hover.leaning}, {hover.mastery},{' '}
              {hover.n_comments} comments, {hover.avg_words?.toFixed(0)} words each</>
          : <>{dots.length} of {subjects.length} subjects are placed.{' '}
              {unplaced.length} are not: {countOf(unplaced, 'mixed')} hold positions that do
              not sit on a single left–right axis and {countOf(unplaced, 'unclear')} left no
              usable evidence. They are left off rather than drawn at the centre,
              which would read as moderate.</>}
      </p>
    </div>
  )
}

function countOf(list, leaning) {
  return list.filter((s) => s.leaning === leaning).length
}
