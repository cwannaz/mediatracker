// MediaTracker logo marks — family style (see ~/cairn, ~/matx, ~/tab):
// 64×64 viewBox, single flat colour via currentColor (so a topbar/favicon can
// recolour per theme, and it works as a CSS mask). Brand gold #FFC000 (dark) /
// #d8a200 (light). Motif: tracking the public conversation — a comment bubble
// with a tracking pulse, plus a feed variant and a tondo ring.

export function PulseMark(props) {
  return (
    <svg viewBox="0 0 64 64" role="img" aria-label="MediaTracker" {...props}>
      <path d="M18 11 H46 Q55 11 55 20 V34 Q55 43 46 43 H28 L20 52 L22.5 43 Q9 43 9 34 V20 Q9 11 18 11 Z"
        fill="none" stroke="currentColor" strokeWidth="4" strokeLinejoin="round" />
      <path d="M16 28 H25 L29.5 20 L34.5 38 L39 28 H48"
        fill="none" stroke="currentColor" strokeWidth="4"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function FeedMark(props) {
  return (
    <svg viewBox="0 0 64 64" role="img" aria-label="MediaTracker" {...props}>
      <rect x="9" y="14" width="46" height="8" rx="4" fill="currentColor" />
      <rect x="9" y="28" width="30" height="8" rx="4" fill="currentColor" />
      <rect x="9" y="42" width="40" height="8" rx="4" fill="currentColor" />
      <circle cx="50" cy="32" r="5.5" fill="currentColor" />
    </svg>
  )
}

export function TondoMark(props) {
  return (
    <svg viewBox="0 0 64 64" role="img" aria-label="MediaTracker" {...props}>
      <circle cx="32" cy="32" r="26" fill="none" stroke="currentColor" strokeWidth="4" />
      <path d="M17 33 H26 L30 24 L35 41 L39.5 33 H47"
        fill="none" stroke="currentColor" strokeWidth="4"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export const LOGO_PROPOSALS = [
  { id: 'pulse', name: 'Pulse bubble', Comp: PulseMark,
    note: 'Comment bubble + tracking pulse — “tracking the conversation”. Adopted.' },
  { id: 'feed', name: 'Tracked feed', Comp: FeedMark,
    note: 'Article feed bars with a tracking node.' },
  { id: 'tondo', name: 'Tondo', Comp: TondoMark,
    note: 'Ring-enclosed pulse — favicon/round-badge sibling.' },
]

export const ADOPTED = 'pulse'
