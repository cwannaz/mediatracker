import { useEffect, useMemo, useRef } from 'react'

// Where the commenting public sits, as a cloud rather than a set of bars.
//
// X is the left–right axis; Y is command of the language. Those are the two
// scales the pass produces that are genuinely ordered, so they are the two
// that can carry a position. Marker area is comment volume, colour is gender.
//
// Subjects the pass would not place — 'mixed' (positions that do not sit on a
// single axis) and 'unclear' (no usable evidence) — are deliberately NOT
// plotted at the centre. Putting them there would invent a moderate reading
// for people who simply never showed one. They are counted underneath instead.
//
// Plotly is loaded the same way the other dashboards in this family load it:
// dynamically, so it stays out of the main bundle, and purged on unmount.

const X = ['far-left', 'left', 'centre-left', 'centre', 'centre-right', 'right', 'far-right']
const Y = ['native-fluent', 'fluent', 'good', 'approximate', 'poor']

const GROUPS = [
  { id: 'm', name: 'male', colour: '#5b8cff' },
  { id: 'f', name: 'female', colour: '#e0699b' },
  { id: 'u', name: 'no gender evidence', colour: '#7d8595' },
]

// A deterministic offset inside a cell: same data, same picture every render.
// Without it 197 subjects collapse onto 35 grid points.
function spread(i) {
  const ring = Math.floor(Math.sqrt(i))
  const around = i - ring * ring
  const ang = (around / Math.max(1, 2 * ring + 1)) * Math.PI * 2 + ring
  return [Math.cos(ang) * ring * 0.075, Math.sin(ang) * ring * 0.075]
}

// Held at module scope so cleanup can purge SYNCHRONOUSLY. Purging from
// inside `import(...).then()` loses a race under StrictMode, which mounts
// every effect twice: the first cleanup's promise resolves after the second
// effect has drawn, and wipes the plot that is meant to stay. React runs
// cleanup synchronously before the next effect, so doing it here cannot race.
let PlotlyMod = null

function themeColours() {
  const cs = getComputedStyle(document.documentElement)
  const v = (n, fallback) => (cs.getPropertyValue(n) || '').trim() || fallback
  return {
    bg: v('--surface-2', v('--bg', '#14151a')),
    grid: v('--border-soft', '#23262f'),
    text: v('--muted', '#9aa0ad'),
  }
}

export default function Compass({ subjects, onOpen }) {
  const ref = useRef(null)
  const openRef = useRef(onOpen)
  openRef.current = onOpen

  const { placed, unplaced } = useMemo(() => {
    const inside = [], out = []
    const cells = new Map()
    for (const s of subjects) {
      const xi = X.indexOf(s.leaning), yi = Y.indexOf(s.mastery)
      if (xi < 0 || yi < 0) { out.push(s); continue }
      const k = `${xi}:${yi}`
      const slot = cells.get(k) || 0
      cells.set(k, slot + 1)
      const [dx, dy] = spread(slot)
      inside.push({ s, x: xi + dx, y: yi + dy })
    }
    return { placed: inside, unplaced: out }
  }, [subjects])

  useEffect(() => {
    const el = ref.current
    if (!el) return
    let purged = false
    let ro = null

    ;(async () => {
      const { default: Plotly } = await import('plotly.js-dist-min')
      PlotlyMod = Plotly
      if (purged) return
      const c = themeColours()
      const maxN = Math.max(1, ...placed.map((p) => p.s.n_comments || 1))

      const group = (s) => (s.male >= 0.6 ? 'm' : s.female >= 0.6 ? 'f' : 'u')
      const data = GROUPS.map((g) => {
        const pts = placed.filter((p) => group(p.s) === g.id)
        return {
          name: g.name,
          // 319 points is nothing; SVG markers style more predictably than
          // scattergl and do not depend on WebGL being available.
          type: 'scatter',
          mode: 'markers',
          x: pts.map((p) => p.x),
          y: pts.map((p) => p.y),
          customdata: pts.map((p) => [
            p.s.label, p.s.leaning, p.s.mastery, p.s.n_comments,
            p.s.avg_words == null ? '—' : p.s.avg_words.toFixed(0),
            p.s.subject_kind, p.s.subject_key,
          ]),
          marker: {
            color: g.colour,
            opacity: 0.62,
            line: { color: g.colour, width: 1 },
            // Area, not radius, carries the volume — sizeref maps the largest
            // subject to a readable disc and everything else in proportion.
            size: pts.map((p) => Math.sqrt((p.s.n_comments || 1) / maxN)),
            sizemode: 'diameter',
            sizeref: 1 / 34,
            sizemin: 4,
          },
          hovertemplate:
            '<b>%{customdata[0]}</b><br>%{customdata[1]} · %{customdata[2]}'
            + '<br>%{customdata[3]} comments · %{customdata[4]} words each<extra></extra>',
        }
      })

      const layout = {
        margin: { l: 128, r: 16, t: 8, b: 56 }, height: 380,
        paper_bgcolor: c.bg, plot_bgcolor: c.bg,
        hoverlabel: { bgcolor: c.bg, bordercolor: c.grid, font: { color: c.text } },
        xaxis: {
          range: [-0.6, X.length - 0.4], tickvals: X.map((_, i) => i), ticktext: X,
          title: { text: 'political leaning', font: { size: 11 } },
          color: c.text, gridcolor: c.grid, zeroline: false, automargin: true,
        },
        yaxis: {
          // Best at the top: the y axis reads as a ranking, not a quantity.
          range: [Y.length - 0.4, -0.6], tickvals: Y.map((_, i) => i), ticktext: Y,
          title: { text: 'language mastery', font: { size: 11 }, standoff: 22 },
          color: c.text, gridcolor: c.grid, zeroline: false, automargin: true,
        },
        legend: { orientation: 'h', y: -0.18, font: { color: c.text, size: 11 } },
        font: { color: c.text, size: 11 },
        shapes: [{
          type: 'line', x0: 3, x1: 3, y0: -0.6, y1: Y.length - 0.4,
          line: { color: c.grid, width: 1, dash: 'dot' },
        }],
      }

      await Plotly.react(el, data, layout, { displayModeBar: false, responsive: true })
      if (purged) return
      el.removeAllListeners?.('plotly_click')
      el.on('plotly_click', (ev) => {
        const cd = ev?.points?.[0]?.customdata
        if (cd) openRef.current?.({ subject_kind: cd[5], subject_key: cd[6] })
      })

      // `responsive` only watches the window, so a plot drawn before the tab's
      // layout settles keeps a width its container never had and overflows.
      // Watch the element itself instead.
      ro = new ResizeObserver(() => { if (!purged) Plotly.Plots.resize(el) })
      ro.observe(el)
      Plotly.Plots.resize(el)
    })()

    return () => {
      purged = true
      ro?.disconnect()
      if (PlotlyMod) {
        try { PlotlyMod.purge(el) } catch { /* never drawn */ }
      }
    }
  }, [placed])

  return (
    <div className="card">
      <h2>The commenting public</h2>
      <div ref={ref} style={{ width: '100%', minHeight: 380 }} />
      <p className="subtle" style={{ marginTop: 6 }}>
        Marker area is comments written; click one to open the subject.{' '}
        {placed.length} of {subjects.length} subjects are placed.{' '}
        {unplaced.length} are not: {unplaced.filter((s) => s.leaning === 'mixed').length} hold
        positions that do not sit on a single left–right axis and{' '}
        {unplaced.filter((s) => s.leaning === 'unclear').length} left no usable
        evidence. They are left off rather than drawn at the centre, which would
        read as moderate.
      </p>
    </div>
  )
}
