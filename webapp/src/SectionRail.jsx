import { useEffect, useState } from 'react'

// A page's own sections, kept in view while the page scrolls.
//
// A commenter page is long -- identity, notes, accounts, metadata, a timeline,
// style measures, a profile and then thousands of comments -- and everything
// above the comments becomes unreachable once you are reading them. The rail
// is that page's table of contents, not navigation between pages.
//
// The sections are declared by the page rather than discovered from the DOM:
// several of them are absent until their data arrives (a nickname with no
// persona has no profile card), and a rail that grew entries as the page
// filled would move under the cursor.
export default function SectionRail({ sections }) {
  const [active, setActive] = useState(sections[0]?.id)

  useEffect(() => {
    const pick = () => {
      // The last heading that has passed under the chrome: what you are
      // reading is the section whose top is above the fold, not the one
      // nearest the middle of the window.
      let best = null
      for (const s of sections) {
        const el = document.getElementById(s.id)
        if (!el) continue
        if (el.getBoundingClientRect().top <= 140) best = s.id
        else break
      }
      // Scrolled to the very bottom, the last section may never cross the
      // line -- a short card after a long one. Then it is the one in view.
      const atEnd = window.innerHeight + window.scrollY
        >= document.documentElement.scrollHeight - 4
      if (atEnd) {
        const last = [...sections].reverse()
            .find((s) => document.getElementById(s.id))
        if (last) best = last.id
      }
      setActive(best ?? sections.find((s) => document.getElementById(s.id))?.id)
    }
    pick()
    window.addEventListener('scroll', pick, { passive: true })
    window.addEventListener('resize', pick)
    return () => {
      window.removeEventListener('scroll', pick)
      window.removeEventListener('resize', pick)
    }
  }, [sections])

  return (
    <nav className="rail" aria-label="Sections of this page">
      {sections.map((s) => (
        <button key={s.id}
                className={`railitem ${active === s.id ? 'on' : ''}`}
                aria-current={active === s.id ? 'true' : undefined}
                onClick={() => document.getElementById(s.id)
                  ?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>
          {s.label}
        </button>
      ))}
    </nav>
  )
}
