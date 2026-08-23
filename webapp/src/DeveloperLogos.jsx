import { LOGO_PROPOSALS, ADOPTED } from './logos.jsx'

// Logo showcase (matx LogosTab convention): every proposal at 16/24/48/96px —
// the favicon, the topbar, and a large view — so a mark can be judged at the
// sizes it must actually survive.
const SIZES = [16, 24, 48, 96]

export default function DeveloperLogos() {
  return (
    <div className="logos">
      <div className="panel-head"><h1>Logos</h1></div>
      <p className="subtle">
        Family style: flat brand gold, 64×64, single colour (theme-recolourable / maskable),
        system fonts. Pick one and I’ll lock it in as the mark + favicon.
      </p>
      {LOGO_PROPOSALS.map(({ id, name, note, Comp }) => (
        <div className="logo-card" key={id}>
          <div className="logo-sizes">
            {SIZES.map((s) => (
              <span className="s" key={s}>
                <Comp width={s} height={s} />
                <small>{s}px</small>
              </span>
            ))}
          </div>
          <div className="logo-meta">
            <h3>{name}{id === ADOPTED && <span className="adopted">● adopted</span>}</h3>
            <div className="subtle">{note}</div>
            <div className="logo-onbar">
              <span className="brand-mark"><Comp width={20} height={20} /></span>
              MediaTracker
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
