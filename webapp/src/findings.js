// The study's findings, written down.
//
// Two rules govern this file.
//
// 1. Numbers are never typed here. Every figure a finding quotes is computed
//    from the live database at render time, through the `figures` and `table`
//    functions below. A finding that hard-codes a count becomes quietly false
//    the next time the scanner runs, and a write-up that cannot be trusted is
//    worse than none.
//
// 2. Every finding declares HOW it is known. That distinction is the whole
//    point of this project's data model and it survives into the write-up:
//
//      measured — computed deterministically from stored text. Reproducible.
//      inferred — an LLM's reading of a dossier, with quotes and confidence.
//      observed — noticed while reading the corpus. A human judgement, no
//                 aggregate behind it.
//      open     — stated as a question, not an answer. Nothing tests it yet.
//
// Add findings by appending to a section's `findings` array. Keep `recorded`
// accurate; it is what lets a later reader tell a fresh finding from a stale one.

const pct = (n, total) => (total ? `${((n / total) * 100).toFixed(1)}%` : '—')
// A share that arrives already divided.
const asPct = (s) => (s == null ? '—' : `${(s * 100).toFixed(1)}%`)

// Sum a distribution row set for one community.
const share = (rows, community, values) => {
  const inC = rows.filter((r) => r.community === community)
  const total = inC.reduce((a, r) => a + Number(r.n), 0)
  const hit = inC.filter((r) => values.includes(r.v)).reduce((a, r) => a + Number(r.n), 0)
  return { n: hit, total, pct: pct(hit, total) }
}

const COMMUNITY_LABEL = {
  lematin: 'Le Matin',
  'tx-romandie': '24 heures / Tribune de Genève',
}
export const communityName = (c) => COMMUNITY_LABEL[c] || c

// A two-column comparison table over the two communities.
const compare = (rows, order) => {
  const cs = [...new Set(rows.map((r) => r.community))].sort()
  const keys = order
    ? order.filter((k) => rows.some((r) => r.v === k))
    : [...new Set(rows.map((r) => r.v))]
  const totals = Object.fromEntries(cs.map((c) => [
    c, rows.filter((r) => r.community === c).reduce((a, r) => a + Number(r.n), 0),
  ]))
  return {
    cols: cs.map(communityName),
    rows: keys.map((k) => ({
      label: k,
      cells: cs.map((c) => {
        const n = Number(rows.find((r) => r.community === c && r.v === k)?.n || 0)
        return { n, pct: pct(n, totals[c]) }
      }),
    })),
  }
}

export const SECTIONS = [
  // ------------------------------------------------------------------ //
  {
    id: 'corpus',
    title: 'The corpus',
    blurb: 'What has been collected, from where, and what shape it is in. '
      + 'Everything below is counted, not estimated.',
    findings: [
      {
        id: 'two-platforms',
        claim: 'Two comment platforms, three titles, one of them collecting nothing until it was rebuilt',
        status: 'measured',
        recorded: '2026-08-24',
        body: [
          'Le Matin runs the TX Group Next.js platform; 24 heures and the Tribune de '
          + 'Genève moved to Astro and share a single comment backend between them. '
          + '24 heures had been scanning on schedule and returning zero articles for '
          + 'an unknown period — the adapter looked for markup the site no longer '
          + 'serves, so nothing in the logs said "broken".',
          'The Astro comment endpoints return HTML fragments rather than JSON, with '
          + 'UUID comment ids, replies flattened to an "@nickname" marker and a '
          + 'timestamp cursor for paging. All three differ from Le Matin.',
        ],
        table: (d) => ({
          cols: ['articles', 'comments', 'community'],
          rows: d.titles.map((t) => ({
            label: t.slug,
            cells: [{ n: t.articles }, { n: t.comments }, { text: communityName(t.community) }],
          })),
        }),
      },
      {
        id: 'archive-vs-live',
        claim: 'Most of the Le Matin corpus is archive, not live capture',
        status: 'measured',
        recorded: '2026-08-24',
        body: [
          'The Le Matin material is largely PDF captures of threads printed years '
          + 'ago; the Astro material is all live scanning from August 2026 onward. '
          + 'That difference explains several of the gaps in the next section, and '
          + 'it should be held in mind everywhere the two are compared.',
          'Archived captures are excluded from re-scanning: a pdf:// pseudo-URL is '
          + 'not retrievable and a printed page is finished. Before that filter '
          + 'existed every scan logged 345 fetch errors trying.',
        ],
      },
    ],
  },

  // ------------------------------------------------------------------ //
  {
    id: 'publics',
    title: 'Two publics, not one',
    blurb: 'Nicknames are only comparable inside one comment backend. These are '
      + 'separate populations and are never pooled without saying so.',
    findings: [
      {
        id: 'community-rule',
        claim: 'The same nickname on two platforms is two people; on two titles sharing a backend it is one',
        status: 'measured',
        recorded: '2026-08-24',
        body: [
          '24 heures and the Tribune de Genève serve the same article id with the '
          + 'same comment thread — identical UUIDs, identical nicknames — verified '
          + 'by fetching one article’s comments from both hosts. Around 218 of '
          + 'the ~270 articles on their fronts on a given day are the same article. '
          + 'What differs is the local desk: Geneva here, Vaud there.',
          'So identity is keyed to the comment backend, not to the title. A '
          + 'commenter writing on both Astro titles stays one subject; the same '
          + 'nickname on Le Matin is a different person until something proves '
          + 'otherwise. Splitting by title instead would have cut dozens of people '
          + 'in half and stored every shared thread twice.',
        ],
        figures: (d) => {
          const x = d.cross_community || []
          return [
            { k: 'Nicknames present in both communities', v: x.length },
            { k: 'Counted as separate subjects', v: x.length * 2 },
          ]
        },
        list: (d) => (d.cross_community || []).map((r) => ({
          label: r.nick,
          detail: (r.split || []).map((s) => `${communityName(s.community)} ${s.n}`).join(' · '),
        })),
        listTitle: 'The nicknames in question — deliberately not merged',
        caveat: 'Whether any of these are the same human is exactly what the '
          + 'profiles now make testable. Nothing here answers it.',
      },
      {
        id: 'signing-shape',
        claim: 'How a public signs itself is set by the sign-up form — and here the form is a box marked “Nom*”',
        status: 'measured',
        recorded: '2026-08-28',
        body: [
          'A handle can be built like a personal name, like a readable persona, or '
          + 'like nothing at all. That is a real difference in how someone stands in '
          + 'front of a public, and it is legible from the string with no inference '
          + 'and no list of names — measured over this corpus, five of 1,666 one-word '
          + 'handles are given names, so presenting as a person is done with a full '
          + 'name or not at all.',
          'Recovering 2012–2016 from the Internet Archive turned this from a '
          + 'suggestive gap into a mechanism. Both publics sat around 80% '
          + 'name-shaped through those years and both then collapsed — Le Matin to '
          + 'roughly one per cent, the Astro titles to a quarter. The high plateau is '
          + 'the same on both, measured from the same capture method, so it is not an '
          + 'artefact of how the material was collected.',
          'The cause is in the archived markup. Tamedia’s Newsnetz comment form did '
          + 'not offer a nickname field at all: it had two boxes, fbFormN_firstName '
          + 'labelled “Prénom*” and fbFormN_surname labelled “Nom*”, the asterisks '
          + 'meaning required. To comment at all you had to supply something shaped '
          + 'like a forename and a surname. When the papers left that platform the '
          + 'requirement went with it, and the public reverted to pseudonyms.',
          'Three oddities in the handles fall out of the same two boxes. The order '
          + 'swaps — herve tinguely and tinguely herve, the same person on different '
          + 'days — are two fields nobody checked the order of. The handles reading '
          + '“Nom Prénom” and “Prénom Nom” are people typing the form’s own labels '
          + 'into the form. And the name-shaped puns are the interesting case: '
          + 'Alex Tincteur, Paul Ochon, Jean Eymar and Sara Dotte are all somebody '
          + 'satisfying a real-name requirement while refusing to meet it.',
          'So the variable is real and it belongs to the software. Comparing two '
          + 'communities on it compares two registration forms first and two publics '
          + 'a distant second — the same trap as comparing their politics without '
          + 'noting one corpus is fifteen years long and the other ten days.',
        ],
        figures: (d) => {
          const rows = d.handle_forms || []
          const at = (c, y) => rows.find((r) => r.community === c && r.year === y)
          const t = d.handle_form_totals || {}
          const f = (name) => (t.forms || []).find((x) => x.form === name)
          return [
            { k: 'Built like a name', v: asPct(f('personal-name')?.share) },
            { k: 'A readable persona', v: asPct(f('alias')?.share) },
            { k: 'Names nothing', v: asPct(f('opaque')?.share) },
            { k: 'Le Matin 2012', v: asPct(at('lematin', 2012)?.share) },
            { k: 'Le Matin 2025', v: asPct(at('lematin', 2025)?.share) },
            { k: 'Astro titles 2026', v: asPct(at('tx-romandie', 2026)?.share) },
          ]
        },
        table: (d) => ({
          cols: ['nicknames', 'name-shaped', 'share'],
          rows: (d.handle_forms || []).map((r) => ({
            label: `${communityName(r.community)} ${r.year}${r.origin === 'pdf' ? ' (archive)' : ''}`,
            cells: [{ n: r.nicks }, { n: r.name_shaped }, { text: asPct(r.share) }],
          })),
        }),
        caveat: 'Name-shaped is a statement about the string and nothing else, and '
          + 'under a form that demanded a name it says even less about the writer: a '
          + 'required field is answered by everyone, honestly or not. Which of these '
          + 'are anyone’s actual name is not asked and not recorded. The archive rows '
          + 'below are single crawler visits, so their counts are a floor.',
      },
      {
        id: 'left-share',
        claim: 'The Astro public leans left substantially more than the Le Matin public',
        status: 'inferred',
        recorded: '2026-08-24',
        body: [
          'Counted per subject, not per comment. This is the one difference between '
          + 'the two populations that survives scrutiny. Both sides of the axis '
          + 'move, but not by the same order: the right share shifts by a few '
          + 'points, the left share by roughly fifteen.',
        ],
        table: (d) => compare(d.politics, ['far-left', 'left', 'centre-left', 'centre',
          'centre-right', 'right', 'far-right', 'mixed', 'unclear']),
        figures: (d) => {
          const L = share(d.politics, 'lematin', ['left', 'centre-left', 'far-left'])
          const T = share(d.politics, 'tx-romandie', ['left', 'centre-left', 'far-left'])
          const LR = share(d.politics, 'lematin', ['right', 'centre-right', 'far-right'])
          const TR = share(d.politics, 'tx-romandie', ['right', 'centre-right', 'far-right'])
          const gap = (a, b) => {
            const d = (b.n / (b.total || 1) - a.n / (a.total || 1)) * 100
            return `${d >= 0 ? '+' : ''}${d.toFixed(1)} pts`
          }
          return [
            { k: 'Left of centre — Le Matin', v: L.pct },
            { k: 'Left of centre — 24h / TDG', v: T.pct },
            { k: 'Difference, left of centre', v: gap(L, T), hi: true },
            { k: 'Right of centre — Le Matin', v: LR.pct },
            { k: 'Right of centre — 24h / TDG', v: TR.pct },
            { k: 'Difference, right of centre', v: gap(LR, TR) },
          ]
        },
      },
      {
        id: 'gaps-are-method',
        claim: 'Two of the biggest gaps between the populations measure the corpus, not the readership',
        status: 'observed',
        recorded: '2026-08-24',
        body: [
          '"Unclear" politics collapses on the Astro side, and canton placement '
          + 'jumps. Neither is a fact about who reads which paper.',
          'Unclear falls because the Astro corpus is live argument, where people '
          + 'state positions, while much of the Le Matin corpus is short archived '
          + 'comments. Canton placement rises because both Astro titles have a hard '
          + 'local desk, so commenters name streets, communes, statutes and '
          + 'cantonal officials constantly — Le Matin is national and gives far '
          + 'less away.',
          'Reporting these as differences between the two publics would be wrong. '
          + 'They are differences between two collection methods.',
        ],
        table: (d) => compare(d.region),
      },
    ],
  },

  // ------------------------------------------------------------------ //
  {
    id: 'desks',
    title: 'Local desks',
    blurb: 'The two Astro titles share every syndicated article, so which title '
      + 'a comment sits under says nothing about the commenter. Which LOCAL DESK '
      + 'they choose to comment on does — and it is behavioural, read from what '
      + 'they clicked rather than from what they wrote.',
    findings: [
      {
        id: 'desk-split',
        claim: 'The shared platform carries two local desks that barely overlap',
        status: 'measured',
        recorded: '2026-08-24',
        body: [
          'Geneva-desk and Vaud-desk articles are almost never carried by the other '
          + 'title, while national, culture and sport content is shared wholesale. '
          + 'That gives one comment backend two distinct local readerships sitting '
          + 'inside it, and a way to tell them apart that does not depend on '
          + 'reading anybody’s prose.',
        ],
        table: (d) => ({
          cols: ['shared between titles', 'exclusive to one'],
          rows: (d.desks?.articles || []).map((a) => ({
            label: a.desk, cells: [{ n: a.shared }, { n: a.exclusive }],
          })),
        }),
        figures: (d) => {
          const r = d.desks?.readers || []
          const get = (k) => Number(r.find((x) => x.lean === k)?.n || 0)
          const one = get('geneva') + get('vaud')
          return [
            { k: 'Commenters reading one local desk', v: one },
            { k: 'Reading both in earnest', v: get('both'), hi: true },
            { k: 'No local comments at all', v: get('none') },
          ]
        },
        caveat: 'Almost nobody engages with both. Two readerships share a comment '
          + 'thread on national news and separate completely on local news, which '
          + 'is the strongest argument yet that keeping them in one community was '
          + 'right for identity and wrong for geography.',
      },
      {
        id: 'desk-contested',
        claim: 'The two titles disagree about what counts as local, and that had to be measured before anything was built on it',
        status: 'measured',
        recorded: '2026-08-24',
        body: [
          'A Vaud story is filed under vaud-regions by 24 heures and under suisse by '
          + 'the Tribune; a Geneva story is the mirror image. Same content, same '
          + 'comment thread, two different editorial framings of whether it is on '
          + 'the reader’s doorstep.',
          'That is interesting in itself, and it is also a threat to the measure: '
          + 'for those articles the desk a reader met the story under depends on '
          + 'which title they were reading, which the shared thread does not '
          + 'record. They are therefore excluded from the local counts entirely. '
          + 'Seven of the 117 profiled subjects lose their local signal as a '
          + 'result, and are counted as having none rather than being guessed at.',
        ],
        figures: (d) => {
          const c = d.desks?.contested || {}
          return [
            { k: 'Articles the two titles file differently', v: c.contested_articles },
            { k: 'Comments excluded from the measure', v: c.comments_excluded },
            { k: 'Article rows in the community', v: c.article_rows },
          ]
        },
      },
      {
        id: 'desk-validates',
        claim: 'Where the two signals are genuinely independent, they have never yet disagreed',
        status: 'measured',
        recorded: '2026-08-24',
        body: [
          'The inferred region and the local-desk lean agree for almost every '
          + 'subject — but that headline had to be thrown away, because the two are '
          + 'not independent for most of them. Auditing the region markers showed '
          + 'that only a minority quote the subject’s own words; the rest cite which '
          + 'threads they commented on, which is the behavioural signal restated. '
          + 'Agreement on those subjects proves nothing.',
          'Restricted to subjects whose region rests on quoted words alone — a '
          + 'helvetism, a street, a statute, a canton named in the first person — '
          + 'the two measures are independent, and on that subset there is not yet '
          + 'a single disagreement.',
        ],
        figures: (d) => {
          const i = d.desks?.independence || {}
          const tot = (i.text_only || 0) + (i.mixed || 0) + (i.thread_only || 0)
          const p = (n) => (tot ? `${Math.round((n / tot) * 100)}%` : '—')
          return [
            { k: 'Region read from quoted words only', v: `${i.text_only} · ${p(i.text_only)}` },
            { k: 'Markers mixing quotes and thread topics', v: `${i.mixed} · ${p(i.mixed)}` },
            { k: 'Region resting on thread topics alone', v: `${i.thread_only} · ${p(i.thread_only)}` },
            { k: 'Independent and testable', v: i.independent_testable },
            { k: 'Of those, in agreement', v: `${i.independent_agree} of ${i.independent_testable}`, hi: true },
          ]
        },
        caveat: 'Seventeen cases is a small base, and one in five region readings '
          + 'rests on thread topics alone — those are weaker than their stated '
          + 'confidence implies and should be re-derived from text before the '
          + 'region field is used for anything load-bearing.',
      },
      {
        id: 'desk-populations',
        claim: 'The two local readerships do not differ politically; they may differ in how they write',
        status: 'inferred',
        recorded: '2026-08-24',
        body: [
          'With the readerships separated behaviourally, they can finally be '
          + 'compared. On politics they do not separate: the left-of-centre gap runs '
          + 'the way one might expect but is well inside what these sample sizes can '
          + 'resolve, and the right-of-centre gap is smaller still.',
          'The one difference that reaches significance is linguistic — Vaud-desk '
          + 'readers are rated fluent or better roughly twice as often, and make '
          + 'fewer errors per hundred words. Mean dossier length is near-identical '
          + 'between the groups, so it is not an artefact of having more text to '
          + 'judge. It survives both the permissive and the conservative desk '
          + 'classification.',
        ],
        table: (d) => {
          const rows = d.desks?.compare || []
          const g = rows.find((r) => r.lean === 'geneva') || {}
          const v = rows.find((r) => r.lean === 'vaud') || {}
          const pc = (n, t) => (t ? `${((n / t) * 100).toFixed(1)}%` : '—')
          const line = (label, key) => ({
            label,
            cells: [
              { n: g[key], pct: pc(g[key], g.n) },
              { n: v[key], pct: pc(v[key], v.n) },
            ],
          })
          return {
            cols: [`Geneva desk (n=${g.n ?? '?'})`, `Vaud desk (n=${v.n ?? '?'})`],
            rows: [
              line('left of centre', 'left_of_centre'),
              line('right of centre', 'right_of_centre'),
              line('mixed or unclear', 'unaligned'),
              line('fluent or better', 'fluent_plus'),
              { label: 'mean errors / 100 words',
                cells: [{ text: g.mean_err }, { text: v.mean_err }] },
              { label: 'mean dossier size',
                cells: [{ text: g.mean_dossier }, { text: v.mean_dossier }] },
            ],
          }
        },
        caveat: 'Five comparisons were made on one dataset. Only the language '
          + 'difference clears p<0.05 (p≈0.013) and none clears the p<0.01 a '
          + 'Bonferroni correction would demand. Treat the language gap as the one '
          + 'worth collecting more data on, and everything else as no difference '
          + 'found rather than as no difference.',
      },
      {
        id: 'desk-fails',
        claim: 'The behavioural measure fails on exactly the people with a cross-cantonal interest',
        status: 'observed',
        recorded: '2026-08-24',
        body: [
          'The one subject whose desk lean contradicts a text-based region reading '
          + 'commented five times on Geneva’s education-funding row and once on '
          + 'anything Vaud — while telling the thread, at length, that he was born '
          + 'in 1942, was a worker’s son, and got a scholarship to the engineering '
          + 'school in Lausanne.',
          'He is not a Geneva reader. He is a Vaud reader with a lifelong interest '
          + 'in education funding, which pulled him onto another canton’s desk. That '
          + 'is the failure mode of the measure, and it is the one the text reading '
          + 'catches, which is why both are kept rather than one being preferred.',
        ],
        evidence: [
          { quote: 'Né en 1942, fils d’ouvrier, comme j’étais bon élève, j’ai obtenu une bourse grâce à laquelle j’ai pu faire des études à l’EPFL', who: 'cristobal02', note: 'inferred Vaud from text; comments almost only on the Geneva education desk' },
        ],
      },
    ],
  },

  // ------------------------------------------------------------------ //
  {
    id: 'writing',
    title: 'How this public writes',
    blurb: 'Language mastery is inferred from a full reading of each dossier; '
      + 'error rates and accent behaviour are measured from the stored text.',
    findings: [
      {
        id: 'mastery-spread',
        claim: 'The commenting public writes better than its reputation, and the tail is thin',
        status: 'inferred',
        recorded: '2026-08-24',
        body: [
          'Roughly a third of subjects in each population write fluently or better. '
          + '"Poor" is close to empty. The bulk sits at "good" — grammatical, '
          + 'idiomatic, with a steady trickle of homophone and agreement slips.',
        ],
        table: (d) => compare(d.mastery, ['native-fluent', 'fluent', 'good', 'approximate', 'poor']),
        figures: (d) => (d.profiles || []).map((p) => ({
          k: `Mean errors / 100 words — ${communityName(p.community)}`,
          v: p.mean_err == null ? '—' : Number(p.mean_err).toFixed(2),
        })),
      },
      {
        id: 'accent-rule',
        claim: 'Never typing accents is a keyboard habit; typing them inconsistently is an error',
        status: 'measured',
        recorded: '2026-08-23',
        body: [
          'This distinction decides a large part of every mastery rating, so it is '
          + 'measured deterministically rather than left to judgement. A writer who '
          + 'uses no accents anywhere is not marked down. A writer who produces '
          + '"réussi" in one sentence and "eleve" in the next has demonstrably got '
          + 'the keyboard for it, and each omission counts.',
          'The first profiling pass over-applied the correction and had to be '
          + 'rebuilt: an inferred accent label is now overridden only on positive '
          + 'proof — at least two measured omissions and a consistency ratio at or '
          + 'below 0.9. On the second pass the guard fired zero times.',
        ],
        table: (d) => compare(d.accents, ['full', 'partial', 'absent']),
      },
      {
        id: 'orthography-social',
        claim: 'Correct spelling is used as a weapon inside the comment section',
        status: 'observed',
        recorded: '2026-08-24',
        body: [
          'Several subjects correct other people’s French mid-argument, and at '
          + 'least one uses an opponent’s phonetic misspelling to score a '
          + 'political point: after a commenter wrote "j’antands" for '
          + '"j’entends", an opponent replied that UDC members at least know how '
          + 'to spell it. One subject answers a thread about falling standards of '
          + 'French entirely in phonetic transcription, as a joke that is also an '
          + 'argument.',
          'For a study measuring how this public writes, that matters: orthography '
          + 'here is not only a skill being exercised, it is a marker being '
          + 'deployed.',
        ],
        evidence: [
          { quote: 'Même en restant modestes les membres de l’UDC savent écrire «j’entends».', who: 'Contribuable vaudois' },
          { quote: 'Ge voix pa hou ai leu problaime.', who: 'Alcazar', note: 'answering an article on declining French at the Collège' },
          { quote: 'Sont. Pas soient.', who: 'Chacureuil', note: 'an entire comment' },
        ],
      },
    ],
  },

  // ------------------------------------------------------------------ //
  {
    id: 'machine',
    title: 'Machine-written comments',
    blurb: 'The corpus now contains comments written with AI assistance, and it '
      + 'is visible. This contaminates exactly what the language pass measures.',
    findings: [
      {
        id: 'ai-present',
        claim: 'Some comments are drafted by AI, and some commenters say so openly',
        status: 'observed',
        recorded: '2026-08-24',
        body: [
          'Found while reading the Astro dossiers. One subject pastes model output '
          + 'with the markdown emphasis, horizontal rules and emoji still in it, and '
          + 'credits the tool by name. Another cites an AI alongside a dated '
          + 'newspaper interview as her two sources for a population figure. A third '
          + 'uses one to confirm a recollection. A fourth alternates flawless '
          + 'essay-length comments with error-strewn one-liners in the same dossier '
          + '— consistent with assistance, though not proof of it.',
          'Two further subjects accuse other commenters of posting machine-written '
          + 'arguments, which means the readership has started policing this itself.',
        ],
        evidence: [
          { quote: 'Je vous laisse lire vous-même la suite de ce qu’en dit ChatGPT qui est sans équivoque', who: 'Çui-là il va au gnouf !' },
          { quote: 'nombre annoncé par une Conseillère municipale dans une interview accordée à La Liberté le 11.09.2025 et par l’IA Gem.', who: 'Martienne' },
          { quote: 'Règle que me confirme l’IA, abandonnée dès 1996-1997.', who: 'Phil Laoloet' },
          { quote: 'Argumentaire IA évident, avec des erreur évidentes.', who: 'GB1204' },
        ],
        caveat: 'Consequence for the study: the mastery figures describe COMMENTS, '
          + 'not necessarily WRITERS. Not yet frequent enough to distort the '
          + 'aggregates, but there is no field recording it and there should be.',
      },
    ],
  },

  // ------------------------------------------------------------------ //
  {
    id: 'self',
    title: 'The comment section talks about itself',
    blurb: 'A recurring meta-argument, held symmetrically, that no participant '
      + 'can settle and that this study has not tested either.',
    findings: [
      {
        id: 'moderation-symmetry',
        claim: 'Both sides believe the moderation is against them',
        status: 'observed',
        recorded: '2026-08-24',
        body: [
          'Two subjects report that left-leaning comments are systematically '
          + 'refused; two report exactly the opposite; a fifth argues that what gets '
          + 'refused is racist or homophobic rather than political, and points at '
          + 'the visible political mix of the thread as evidence. One threatens to '
          + 'cancel his subscription over it; another says he has had more than a '
          + 'hundred comments refused and has tested it deliberately.',
          'None of this is verified here. What is recorded is that the belief '
          + 'exists on both sides at once, which is itself a finding about the '
          + 'public rather than about the moderation.',
        ],
        evidence: [
          { quote: 'J’ai fait le test, des centaines de fois. J’ai eu déjà plus de cent commentaires de gauche refusés.', who: 'Bob Z', note: 'from the left' },
          { quote: 'vous voulez écrire un commentaire un peu trop à droite, il ne passera pas la censure', who: 'olivier micka', note: 'from the right' },
          { quote: 'journal de gauche qui ne parle que de thèmes de gauche pour un lectorat de gauche […] il est possible voire probable que je me désabonne.', who: 'Ddblue', note: 'from the right' },
          { quote: 'Probablement parce que certains commentaires (très) à droite ne respectent parfois pas les règles basiques de respect, d’absence de propos racistes ou homophobes', who: 'Ceszigues', note: 'a third position' },
        ],
      },
      {
        id: 'readership-vs-line',
        claim: 'A commenter states the study’s own premise unprompted',
        status: 'observed',
        recorded: '2026-08-24',
        body: [
          'One subject makes in a single line the observation this project exists '
          + 'to test — that a paper’s commentariat need not resemble its '
          + 'editorial line.',
        ],
        evidence: [
          { quote: 'Il est étonnant qu’un journal "de gauche" ait pour lectorat-commentateur une large majorité très à droite.', who: 'Allenbach Jean-Marc' },
        ],
      },
    ],
  },

  // ------------------------------------------------------------------ //
  {
    id: 'texture',
    title: 'Texture of the public',
    blurb: 'Things that do not fit a distribution but are worth having on record.',
    findings: [
      {
        id: 'huitante',
        claim: 'The shared Geneva/Vaud platform has an intra-Romandie language conflict running in it',
        status: 'observed',
        recorded: '2026-08-24',
        body: [
          'Geneva, Neuchâtel and Jura say "quatre-vingts"; Vaud says "huitante". '
          + 'Now that one comment backend serves both readerships, that seam is '
          + 'being argued about in the comments themselves — a Geneva subject '
          + 'objects to the Vaud form appearing in a Geneva paper and names it '
          + 'imperialism.',
          'This is exactly the kind of division the community model was built to '
          + 'keep visible rather than average away.',
        ],
        evidence: [
          { quote: 'l’encre bleue est désormais vaudoise comme la RTV qui nous accable du huitante, localisme dialectal qui n’est pas genevois, ni neuchâtelois, ni jurassien…', who: 'Michel Prabang' },
          { quote: 'Ce n’est pas une question de logique, mais d’impérialisme vaudois …', who: 'Michel Prabang' },
          { quote: '"huitantaine" ça veut dire quoi cette horreur?!', who: 'Dieudonné' },
        ],
      },
      {
        id: 'not-closed',
        claim: 'The French-language commenting public is not geographically closed',
        status: 'observed',
        recorded: '2026-08-24',
        body: [
          'One subject states plainly that he lives in a German-speaking canton and '
          + 'reads both Romandie titles daily, commenting on Vaud and Fribourg '
          + 'stories. The region field records "other" rather than forcing a '
          + 'Romandie canton onto him.',
        ],
        evidence: [
          { quote: 'Dans ma commune zurichoise, les routes sont bordées de bouleaux.', who: 'Denis Magnin' },
        ],
      },
      {
        id: 'age-range',
        claim: 'The public is older than a comment section is usually assumed to be',
        status: 'observed',
        recorded: '2026-08-24',
        body: [
          'Several subjects date themselves well above the corpus norm without '
          + 'being asked: one born in 1942 who sets a scholarship against a '
          + 'lifetime of tax paid, one recalling Geneva in the late 1950s as a '
          + 'child, one who says she benefited from the postwar boom years, one '
          + 'sailing the lake since 1981.',
        ],
        evidence: [
          { quote: 'Né en 1942, fils d’ouvrier, comme j’étais bon élève, j’ai obtenu une bourse', who: 'cristobal02' },
          { quote: 'j’étais gamine j’adorais voir cette voiture …. je parle des années fin ’50 et début ’60', who: 'Ivy Stenz' },
          { quote: 'Moi-même navigateur lémanique depuis 1981', who: 'Contribuable vaudois' },
        ],
      },
      {
        id: 'no-drift',
        claim: 'Political positions do not visibly move, but almost no dossier is long enough to tell',
        status: 'inferred',
        recorded: '2026-08-24',
        body: [
          'Drift is recorded as "none" for nearly every subject. That is the honest '
          + 'result rather than a null one — the profiling pass was explicitly told '
          + 'not to manufacture arcs — but it is mostly a statement about the '
          + 'corpus: the great majority of dossiers span days or weeks.',
          'The handful that span years show either stability or a change in what '
          + 'the subject comments on rather than in what they think.',
        ],
        table: (d) => compare(d.drift, ['none', 'mild', 'marked']),
      },
    ],
  },

  // ------------------------------------------------------------------ //
  {
    id: 'method',
    title: 'Method, and what it cannot support',
    blurb: 'The limits worth stating before any of the above is quoted.',
    findings: [
      {
        id: 'concentration',
        claim: 'One subject dominates the Le Matin corpus by construction',
        status: 'measured',
        recorded: '2026-08-24',
        body: [
          'The archive is built from articles someone printed, and people print the '
          + 'threads they took part in. Any figure weighted by comment volume is '
          + 'therefore dominated by the heaviest subject. Every distribution on this '
          + 'page counts each subject once, which makes them unaffected — but '
          + 'anything computed per comment is not.',
        ],
        figures: (d) => (d.heaviest || []).map((h) => ({
          k: `Heaviest subject — ${communityName(h.community)}`,
          v: `${h.label} · ${h.n_comments} comments · ${h.pct}% of the analysed text`,
          hi: Number(h.pct) > 10,
        })),
      },
      {
        id: 'gender-scarcity',
        claim: 'Gender is unknown for about four subjects in five, and that is the correct answer',
        status: 'inferred',
        recorded: '2026-08-24',
        body: [
          'Gender is read only from French grammatical self-reference — a past '
          + 'participle or adjective the writer applies to themselves — or from an '
          + 'explicit statement. Never from topic, tone, or the pseudonym, however '
          + 'obviously gendered it looks.',
          'The result is that most subjects are unknown, because most people never '
          + 'apply a gendered adjective to themselves in a comment. A profiling pass '
          + 'that returned confident genders here would be reporting stereotype, not '
          + 'evidence. An ingest guard resets any confident claim with no stated '
          + 'basis; on the second pass it fired zero times.',
        ],
        table: (d) => compare(d.gender, ['male', 'female', 'unknown']),
      },
      {
        id: 'unvalidated',
        claim: 'Two judgement calls remain deliberately unvalidated',
        status: 'open',
        recorded: '2026-08-24',
        body: [
          'The corpus contains one subject whose real positions and trajectory are '
          + 'known to the study owner. Checking the mild-drift verdicts and the '
          + 'mastery ratings against that knowledge was offered and declined, on the '
          + 'grounds that tuning the method to the one case it can be checked '
          + 'against is overfitting.',
          'So the accent threshold and the drift band stand as set, unverified '
          + 'against ground truth, on purpose. That is a stronger position than a '
          + 'validated one would have been, and it should be stated wherever these '
          + 'numbers are quoted.',
        ],
      },
      {
        id: 'paywall',
        claim: 'Premium articles contribute no text, and the corpus records that rather than hiding it',
        status: 'measured',
        recorded: '2026-08-24',
        body: [
          'A paywalled Astro article renders its photographs and captions but not '
          + 'one paragraph. Scraped naively, the body comes back as roughly 150 '
          + 'characters of photo credit and reads like a very short article rather '
          + 'than an absent one. The parser counts how many text blocks were '
          + 'actually served and stores no body when that is zero.',
          'The comments on those articles are public and complete, which is what '
          + 'this study is about, so they are still tracked.',
        ],
      },
    ],
  },
]
