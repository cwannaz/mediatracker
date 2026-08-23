import { useEffect, useState } from 'react'
import { useDaemon } from './useDaemon.js'
import DataSources from './DataSources.jsx'
import DeveloperLogos from './DeveloperLogos.jsx'
import { PulseMark } from './logos.jsx'

const TABS = [
  { id: 'sources', label: 'Data Sources' },
  { id: 'browser', label: 'Article Browser' },  // built later
  { id: 'dev', label: 'Developer' },
]

export default function App() {
  const { connected, send } = useDaemon()
  const [tab, setTab] = useState('sources')
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
              onClick={() => setTab(t.id)}
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

      {tab === 'sources' && <DataSources connected={connected} send={send} />}
      {tab === 'browser' && (
        <div className="placeholder">
          The article &amp; comment browser will live here — coming next.
        </div>
      )}
      {tab === 'dev' && <DeveloperLogos />}
    </>
  )
}
