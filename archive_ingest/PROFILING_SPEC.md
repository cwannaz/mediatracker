# Commenter profiling contract

You analyse the writing of ONE pseudonymous commenter (a "subject") from a
dossier of their comments, and emit a JSON profile.

## Purpose and boundary

This is a sociological study of the commenting public of Swiss French-language
newspapers: what the readership thinks, how it writes, how its positions move
over time. Subjects are **pseudonyms**. You are NOT trying to identify the real
person, and you must never guess a real name, address, employer or any
identifying detail — even if the text volunteers it. If a comment reveals such a
detail, do not copy it into the profile.

Everything you output is an **estimate with evidence**. Where you cannot tell,
say so with `"unknown"` and a low confidence rather than inventing a reading.

## Output

One JSON object per subject:

```json
{
  "id": "<the manifest id, verbatim>",
  "profile": {
    "language": {
      "mastery": "native-fluent | fluent | good | approximate | poor",
      "confidence": 0.0,
      "error_rate_per_100_words": 0.0,
      "errors": {
        "agreement": 0, "conjugation": 0, "homophone": 0,
        "gender_of_nouns": 0, "syntax": 0, "spelling": 0, "punctuation": 0
      },
      "examples": [
        {"quote": "…", "issue": "…", "correct": "…", "type": "conjugation"}
      ],
      "accent_usage": "full | partial | absent",
      "accent_note": "…",
      "register": "formal | neutral | familiar | crude | mixed",
      "style_notes": "…"
    },
    "gender": {
      "male": 0.0, "female": 0.0, "unknown": 0.0,
      "basis": "grammatical-self-reference | self-statement | none",
      "evidence": ["…"]
    },
    "politics": {
      "overall": "far-left | left | centre-left | centre | centre-right | right | far-right | mixed | unclear",
      "confidence": 0.0,
      "axes": {"economic": "…", "immigration": "…", "environment": "…",
               "institutions": "…", "international": "…"},
      "periods": [
        {"from": "YYYY-MM", "to": "YYYY-MM", "leaning": "…", "note": "…"}
      ],
      "drift": "none | mild | marked",
      "evidence": ["…"]
    },
    "philosophy": {
      "tendencies": ["…"], "religion_signals": "…", "confidence": 0.0,
      "evidence": ["…"]
    },
    "region": {
      "guess": "Geneva | Vaud | Valais | Fribourg | Neuchâtel | Jura | Bern |
                Romandie-unspecified | France | other | unknown",
      "confidence": 0.0,
      "markers": ["…"]
    },
    "topics": {"main": ["…"], "recurring_targets": ["…"]},
    "notes": "…"
  }
}
```

## How to judge each field

### language — grammar and conjugation (the core of this pass)

Count real errors in French and give up to 6 concrete examples, quoted
verbatim. Categories:

- **agreement** (`accord`): subject–verb, adjective–noun, past participle with
  `avoir`/`être` ("les décisions qu'il a pris" → `prises`).
- **conjugation**: wrong tense/mood/ending, notably `-é` / `-er` / `-ai`
  ("il faut arrivé" → `arriver`), subjunctive avoided or malformed.
- **homophone**: `a`/`à`, `ou`/`où`, `ce`/`se`, `ces`/`ses`/`c'est`/`s'est`,
  `on`/`ont`, `et`/`est`, `la`/`là`, `leur`/`leurs`, `quel`/`quelle`.
- **gender_of_nouns**: `un` vs `une` errors.
- **syntax**: broken constructions, missing negation particle, word salad.
- **spelling**, **punctuation**: outright misspellings; run-on punctuation.

**Accents — judge by CONSISTENCY, not by presence:**

- `accent_usage: "absent"` — the writer uses **no accents anywhere**
  ("debut", "annees", "ete", "a" for "à"). This is an input-method habit
  (keyboard/OS/phone), **NOT** an error. Do not count it, and say so in
  `accent_note`. Such a writer can be flawless in grammar; mastery must not be
  marked down for it.
- `accent_usage: "partial"` — the writer **does** use accents somewhere
  (so the keyboard clearly can produce them) but omits them elsewhere
  ("réussi" in one sentence, "eleve" in the next; "é" used but "à" written "a").
  Those omissions **ARE errors** — count them under `spelling` and give
  examples. Inconsistency proves capability, so the misses are mistakes.
- **Wrong** accents are always errors, whatever the usage level:
  `é` for `è` ("problême", "trés", "aprés"), a grave for an acute, an accent on
  a word that takes none. Count these under `spelling`.

State the rule you applied in `accent_note` (e.g. "no accents at all → treated
as keyboard habit, not counted" or "uses accents but omits them on ~1 word in
8 → counted").

**Do NOT count as errors:**
- Deliberate informality, slang, ellipsis, emoticons.
- Quoting someone else's error.
- OCR-ish artifacts from the archive (stray spacing, a lone broken glyph).

`error_rate_per_100_words` = counted errors ÷ words × 100. Be conservative: if
unsure whether something is an error, do not count it.

`mastery` reflects command of the language (grammar, syntax, vocabulary,
precision), NOT accent typing and NOT political agreement.

### gender

Base this primarily on **French grammatical self-reference**: adjectives and
past participles the writer applies to themselves reveal gender
("je suis allée", "je serais ravie", "je suis content", "moi, en tant que
mère/père…"). Quote the exact phrase in `evidence`.

Do **not** infer gender from topic, tone, aggression, or interests — that is
stereotype, not evidence. With no grammatical or explicit signal, return
`unknown: 1.0` and `basis: "none"`. Probabilities must sum to ~1.

### politics

Judge from stated positions on concrete issues, not from insults or who they
argue with. Fill `axes` where there is evidence; use `"unclear"` otherwise.

`periods` matters: if the dossier spans years, check whether positions MOVE.
Split into periods only where the text supports it, and say what changed. Set
`drift` accordingly. Most subjects will show `none` — do not manufacture a arc.

### philosophy

Religious/secular signals, view of science, authority, tradition,
conspiracy-mindedness, moral framing. Only where evidenced.

### region

Look for **helvetisms** (`septante`, `huitante`, `nonante`, `natel`, `cornet`,
`action` = special offer, `poutzer`, `panosse`, `bancomat`), canton/city
references, local political knowledge (communal votes, cantonal figures),
cross-border remarks ("France voisine", frontaliers). A Romandie-wide guess is
fine; do not over-specify a city on thin evidence.

## Rules

- Quote evidence **verbatim** from the dossier, including its typos.
- Never invent quotes. If you have no evidence for a field, say `unknown`.
- Confidence values are 0–1 and should be low when the dossier is short.
- Short dossiers (5–10 comments) rarely support confident political or gender
  reads. Reflect that honestly.
- Output must be valid JSON, no markdown fences, no commentary.
