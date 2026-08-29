# Where else the missing years could come from

Compiled 2026-08-28. The corpus jumps from 2012 to 2021 on Le Matin, and 24
heures and the Tribune have nothing before August 2026.

**The finding that governs the whole list: none of the paid options carry
reader comments.** Press databases index what the newsroom published, not what
the public wrote underneath it. A subscription buys article text and metadata —
useful for dating threads, naming sections and measuring what the paper covered
— and buys nothing at all of the object of this study. The only source anywhere
that holds the comments is the Internet Archive, because Tamedia's old platform
rendered them server-side.

Spend money, if at all, on articles. Spend patience on the archive.

---

## Free, and already running

**Internet Archive (Wayback Machine)** — `mediatracker/backfill.py`.
The only source with comments. Le Matin thread captures: 11,533 (2014), 16,395
(2015), 11,725 (2016), then a cliff to single digits when the paper left the
Newsnetz platform. 24 heures and the Tribune are on the same stack and the same
years. No account, no cost; the constraint is politeness — one request every
two seconds, so a year of Le Matin is roughly fourteen hours.

## Free, worth adding — articles only

**Scriptorium** (BCU Lausanne) — <https://scriptorium.bcu-lausanne.ch>
Le Matin from 1862 and 24 heures from 1762, digitised, full-text searchable,
free to the public. Two caveats that matter:

* A **moving wall excludes roughly the last ten years**, so it reaches about
  2016 — which happens to be exactly the article gap.
* It is the **print edition**, not the website. That is a different corpus from
  the one the comments hang off: a print article has no URL, no thread and no
  comment count, so it cannot be joined to anything already here. Valuable as a
  record of what the paper ran; not a substitute for the web article.

**e-newspaperarchives.ch** (Swiss National Library) — 226 titles, 17M pages,
free. Broad Swiss coverage, weighted historical. Same print-edition caveat.

**letempsarchives.ch** — Le Temps, Journal de Genève, Gazette de Lausanne.
Different papers entirely, so only useful as a comparison corpus for how
Romandie's press covered the same events.

## Application required — probably free, worth asking

**Swissdox@LiRI** (University of Zurich) — <https://www.liri.uzh.ch/en/services/swissdox.html>
The strongest article source by a distance: ~24 million articles from 260 Swiss
sources including **TX Group, so all three of our titles**, spanning decades and
updated daily. Access is by registering a research project through LiRI, who
front the commercial SMD database for researchers.

**The open question is eligibility.** It is built for academic projects at Swiss
institutions, and this is private research by an individual. That is worth one
email rather than an assumption — if it is open to unaffiliated researchers, it
solves the article half of the problem outright and makes Scriptorium and the
Wayback `articles` pass unnecessary.

## Commercial

**SMD (Schweizer Mediendatenbank)** — the commercial database behind Swissdox.
Direct licensing; priced for newsrooms and agencies.

**Factiva** (Dow Jones) and **LexisNexis** — international aggregators. Not
verified as carrying these three titles; both are per-seat and expensive, and
both are article-only. Hard to justify given Swissdox covers the same ground.

---

## Recommendation

1. Let the Wayback backfill run. It is free and it is the only comment source.
2. Send one email to LiRI about Swissdox eligibility. Highest value per minute
   on this list.
3. Treat Scriptorium as a separate print corpus if the study ever wants to ask
   what the paper published versus what the website did — not as gap-filling.
4. Do not subscribe to anything before (2) is answered.
