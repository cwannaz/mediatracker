import { useCallback, useEffect, useMemo, useState } from 'react'

// The app's location, kept in the URL.
//
// Every view used to live in React state alone, which left the browser's Back
// button with nothing of ours to go back to: it walked out of the app to
// whatever preceded it in the tab's history — often the daemon's own port,
// visited once while debugging. Now each view a click can reach is a history
// entry, so Back means "the view before this one".
//
// The route is just the hash split on '/': ['browser', 'articles', '<id>'].
// Segments are percent-encoded, which matters for the ones that carry a
// nickname — they contain spaces, and occasionally a slash.

function parse() {
  return window.location.hash.replace(/^#\/?/, '')
    .split('/')
    .filter(Boolean)
    .map((s) => { try { return decodeURIComponent(s) } catch { return s } })
}

function build(parts) {
  return '#/' + parts.filter((p) => p != null && p !== '')
    .map(encodeURIComponent).join('/')
}

// How many entries this session has pushed. Kept in history.state so it
// survives Back and Forward, and so a view opened by a pasted link — which
// carries no state — is correctly recognised as having nothing behind it.
const depth = () => window.history.state?.mtDepth ?? 0

export function useRoute() {
  const [path, setPath] = useState(parse)

  useEffect(() => {
    const sync = () => setPath(parse())
    window.addEventListener('popstate', sync)   // Back / Forward
    window.addEventListener('hashchange', sync) // hand-edited URL
    return () => {
      window.removeEventListener('popstate', sync)
      window.removeEventListener('hashchange', sync)
    }
  }, [])

  const navigate = useCallback((parts, { replace = false } = {}) => {
    const hash = build(parts)
    if (hash === window.location.hash) return
    const d = replace ? depth() : depth() + 1
    window.history[replace ? 'replaceState' : 'pushState']({ mtDepth: d }, '', hash)
    setPath(parse())   // neither pushState nor replaceState fires an event
  }, [])

  // Undo the last step. The app's own Back controls defer to the browser's
  // history when this session put an entry there, so the two never disagree;
  // a view reached by a pasted link has nothing behind it and falls back to
  // replacing itself with the list it belongs to.
  const back = useCallback((fallback) => {
    if (depth() > 0) window.history.back()
    else navigate(fallback, { replace: true })
  }, [navigate])

  return [path, navigate, back]
}

// Hand a child component the part of the route below it, and a navigate that
// writes back into that part. A child never needs to know its own prefix.
export function useSubRoute(path, navigate, back, depth) {
  const head = useMemo(() => path.slice(0, depth), [path, depth])
  const sub = useMemo(() => path.slice(depth), [path, depth])
  const go = useCallback((parts, opts) => navigate([...head, ...parts], opts),
    [head, navigate])
  const goBack = useCallback((fallback = []) => back([...head, ...fallback]),
    [head, back])
  return [sub, go, goBack]
}
