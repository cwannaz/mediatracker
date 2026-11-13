import { useEffect, useRef } from 'react'

// Where each view was scrolled to, so coming back lands where you left.
//
// The browser cannot do this for us. Every view here is one document whose
// body React replaces, so there is no per-page scroll position to restore --
// and the history entries are hash changes, which the browser's own scroll
// restoration ignores. Clicking an article from three thousand comments down
// and coming back to the top makes the comment list unusable.
//
// Two halves, and the second is the awkward one:
//
//   * the position is recorded as it changes, not when the route changes. By
//     the time a route effect runs, React has already rendered the incoming
//     view, the document has usually become shorter, and the browser has
//     clamped scrollY -- so reading it then records the wrong number.
//
//   * restoring has to wait for the view to have height. Views fetch their
//     own data and render "Loading…" first, so the document is a few hundred
//     pixels tall for a moment and a scroll to 9,000 goes nowhere. So the
//     restore is attempted until it sticks, and gives up rather than fighting
//     the reader: any real scroll, key or wheel abandons it.
const CAP_MS = 4000

export function useScrollMemory(key) {
  const spots = useRef(new Map())
  const current = useRef(key)

  // Record. A passive listener, and the key is read from a ref so that the
  // position always lands under the view that was actually scrolled.
  useEffect(() => {
    const note = () => spots.current.set(current.current, window.scrollY)
    window.addEventListener('scroll', note, { passive: true })
    return () => window.removeEventListener('scroll', note)
  }, [])

  // Restore.
  useEffect(() => {
    if (current.current === key) return undefined
    current.current = key
    const target = spots.current.get(key) ?? 0
    if (!target) {
      window.scrollTo(0, 0)
      return undefined
    }

    let raf = 0
    let done = false
    const started = Date.now()
    const stop = () => {
      if (done) return
      done = true
      window.cancelAnimationFrame(raf)
      for (const ev of ['wheel', 'touchstart', 'keydown']) {
        window.removeEventListener(ev, stop)
      }
    }
    const tick = () => {
      if (done) return
      if (Math.abs(window.scrollY - target) > 2) window.scrollTo(0, target)
      // Reached it, or the page can never be that tall: either way, stop.
      if (Math.abs(window.scrollY - target) <= 2 || Date.now() - started > CAP_MS) {
        stop()
        return
      }
      raf = window.requestAnimationFrame(tick)
    }
    for (const ev of ['wheel', 'touchstart', 'keydown']) {
      window.addEventListener(ev, stop, { passive: true, once: true })
    }
    raf = window.requestAnimationFrame(tick)
    return stop
  }, [key])
}
