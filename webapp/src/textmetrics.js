// Deterministic descriptive style measures over a commenter's texts.
// These are objective counts, NOT a judgement of competence — the LLM-inferred
// attributes (gender, leaning, region) come from the profiling pass instead.

const WORD_RE = /[\p{L}\p{M}'’-]+/gu
const ACCENTED_RE = /[àâäçéèêëîïôöùûüÿœæ]/i

export function languageMetrics(texts) {
  const empty = { avgWords: '—', avgSentence: '—', ttr: '—', accentRate: '—', capsRate: '—', exclam: '—' }
  const list = (texts || []).filter(Boolean)
  if (!list.length) return empty

  let words = 0, sentences = 0, caps = 0, accented = 0, exclam = 0
  const vocab = new Set()

  for (const t of list) {
    const ws = t.match(WORD_RE) || []
    words += ws.length
    for (const w of ws) {
      vocab.add(w.toLowerCase())
      if (w.length > 2 && w === w.toUpperCase() && /\p{L}/u.test(w)) caps++
      if (ACCENTED_RE.test(w)) accented++
    }
    sentences += (t.match(/[.!?…]+(\s|$)/g) || []).length || 1
    exclam += (t.match(/!/g) || []).length
  }

  const r1 = (x) => (Math.round(x * 10) / 10).toFixed(1)
  const pct = (x) => `${(x * 100).toFixed(1)}%`
  return {
    avgWords: r1(words / list.length),
    avgSentence: sentences ? r1(words / sentences) : '—',
    ttr: words ? pct(vocab.size / words) : '—',
    accentRate: words ? pct(accented / words) : '—',
    capsRate: words ? pct(caps / words) : '—',
    exclam: r1(exclam / list.length),
  }
}
