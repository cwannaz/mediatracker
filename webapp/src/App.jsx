import { useEffect, useState } from 'react'
import { useDaemon } from './useDaemon.js'
import { useRoute, useSubRoute } from './route.js'
import DataSources from './DataSources.jsx'
import DeveloperLogos from './DeveloperLogos.jsx'
import Browser from './Browser.jsx'
import Search from './Search.jsx'
import Findings from './Findings.jsx'
import { PulseMark } from './logos.jsx'

// Search leads because the corpus is no longer primarily a study of
// commenters. It is a record of what these papers published and what their
// readers made of it, and the commenters are one branch of that — a rich one,
// with its own subtabs under Browse, but not the entrance any more.
const TABS = [
  { id: 'search', label: 'Search' },
  { id: 'browser', label: 'Browse' },
  { id: 'findings', label: 'Findings' },
  { id: 'sources', label: 'Data Sources' },
  { id: 'dev', label: 'Developer' },
]

export default function App() {
  const { connected, send } = useDaemon()
  const [path, navigate, back] = useRoute()
  const tab = TABS.some((t) => t.id === path[0]) ? path[0] : TABS[0].id
  // Give the app a base history entry, so the first Back inside it lands on a
  // view of ours rather than on whatever the tab held before.
  useEffect(() => { if (path[0] !== tab) navigate([tab], { replace: true }) }, [path, tab, navigate])
  const [sub, go, goBack] = useSubRoute(path, navigate, back, 1)
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem('mt-theme') || 'dark' } catch { return 'dark' }
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try { localStorage.setItem('mt-theme', theme) } catch { /* ignore */ }
  }, [theme])

  return (
    <>
      <header className="topbar">
        <span className="brand">
          <span className="brand-mark"><PulseMark width={22} height={22} /></span>
          MediaTracker
        </span>
        <nav className="tabs" aria-label="Sections">
          {TABS.map((t) => (
            <button
              key={t.id}
              className="tab"
              aria-current={tab === t.id ? 'page' : undefined}
              onClick={() => navigate([t.id])}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <span className="spacer" />
        <span className={connected ? 'pill ok' : 'pill down'}>
          {connected ? 'daemon connected' : 'daemon offline'}
        </span>
        <button className="iconbtn" title="Toggle theme"
          onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
          {theme === 'dark' ? '☾' : '☀'}
        </button>
      </header>

      {tab === 'search' && (
        <Search connected={connected} send={send}
                /* Search hands back a Browse path (e.g. a commenter), so it is
                   rooted at that tab rather than at the top level. */
                navigate={(p) => navigate(['browser', ...p])} />
      )}
      {tab === 'sources' && <DataSources connected={connected} send={send} route={sub} navigate={go} />}
      {tab === 'browser' && <Browser connected={connected} send={send} route={sub} navigate={go} back={goBack} />}
      {tab === 'findings' && <Findings connected={connected} send={send} route={sub} navigate={go} />}
      {tab === 'dev' && <DeveloperLogos />}
    </>
  )
}
