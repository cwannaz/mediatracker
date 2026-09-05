"""What commenters say about their own circumstances, in their own words.

The inferred half of a profile guesses at a subject: an LLM reads the dossier
and estimates leaning, region, gender. This does the opposite and only reports
what the person stated outright -- "je suis retraité", "mon salaire net était
de 3156.- par mois", "j'habite à 15 km de Zurich". Nothing is inferred and
nothing is aggregated into a class or a bracket, because the sentence is
better evidence than any label derived from it. The card shows the quote.

Across the corpus roughly 14,000 comments from 5,000 nicknames carry one of
these, which is what makes a socio-economic reading possible at all: the
platform collects no demographics, so self-disclosure is the only route to
occupation, income, tenure or household there has ever been.

**A first-person pattern is not a disclosure, and three things make it lie.**
Each is handled here because each produced false positives when tried:

  * **Negation.** "je ne suis pas médecin" matches every occupation pattern
    and means the reverse. So does "je ne suis plus", "je n'ai jamais été".
  * **Hypotheticals.** "si j'étais retraité", "imaginez que j'habite", "quand
    je serai à la retraite" are arguments, not circumstances. The conditional
    and future tenses are the tell, and so is a preceding `si`.
  * **Quoted speech.** This population argues by quoting each other -- the
    highest-stance writers open every comment with `@nick` and quote them back
    -- so a disclosure inside quotation marks is very often somebody else's.
    Text between « » or " " is removed before matching.

**On identifiability.** Cedric's read is that this population does not post
names, addresses or numbers, and the samples bear that out. Worth saying once
anyway: occupation, commune and age are individually harmless and jointly
narrow -- "je suis médecin" plus "j'habite à 15 km de Zurich" plus "j'ai 62
ans" is a small set of people. That is a property of the material, not of this
module, and it argues for keeping the quote in view rather than reducing
people to a demographic tuple. Which is what this does.
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict

log = logging.getLogger(__name__)

# One entry per socio-economic dimension. Patterns are deliberately narrow:
# a missed disclosure costs one quote, a false one puts words in someone's
# mouth on a page headed with their name.
CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("occupation", "Occupation",
     r"je suis (?:un |une )?(?:retrait\w+|chôm\w+|indépendant\w*|fonctionnaire|"
     r"étudiant\w*|apprenti\w*|infirmi\w+|enseignant\w*|professeur\w*|médecin|"
     r"ouvri\w+|employé\w*|patron\w*|agriculteur\w*|paysan\w*|artisan\w*|"
     r"cadre|ingénieur\w*|informaticien\w*|juriste|avocat\w*|policier\w*|"
     r"militaire|camionneur\w*|chauffeur\w*|vendeu\w+|serveu\w+|maçon\w*|"
     r"électricien\w*|mécanicien\w*|boulanger\w*|coiffeu\w+|assistant\w*)"),

    ("work", "Work",
     r"(?:je travaille (?:dans|comme|chez|à|au|en|pour)|"
     r"j'ai travaillé (?:dans|comme|chez|pendant)|"
     r"mon (?:patron|employeur|métier|boulot|travail|entreprise)|"
     r"ma (?:boîte|société|profession)|je bosse (?:dans|comme|chez))"),

    ("income", "Income and money",
     r"(?:mon salaire|je gagne|ma rente|mon revenu|mon loyer|ma prime "
     r"d'assurance|ma caisse maladie|mes impôts|je paie \d|mon deuxième "
     r"pilier|mon 2ème pilier|mon avs|mon chômage|mon budget)"),

    ("housing", "Housing",
     r"(?:j'habite (?:à|au|en|dans|depuis)|je vis (?:à|au|en|dans|depuis)|"
     r"je suis propriétaire|je suis locataire|mon appartement|ma maison|"
     r"mon immeuble|mon quartier|mon village|ma commune|je loue un)"),

    ("household", "Household and family",
     r"(?:ma femme|mon mari|mon épouse|mon époux|ma compagne|mon compagnon|"
     r"mes enfants|mon fils|ma fille|mes petits-enfants|mes parents|"
     r"je suis divorcé\w*|je suis marié\w*|je suis célibataire|je suis veu\w+|"
     r"je suis (?:père|mère) de)"),

    ("age", "Age and generation",
     r"(?:j'ai \d{2} ans|à mon âge|je suis né\w* en \d{4}|ma génération|"
     r"j'ai passé la (?:cinquantaine|soixantaine|quarantaine)|"
     r"depuis mes \d{2} ans)"),

    ("education", "Education",
     r"(?:j'ai fait (?:des études|un apprentissage|l'université|une "
     r"formation)|mon diplôme|mon apprentissage|mon cfc|mon master|"
     r"ma maturité|j'ai étudié)"),

    ("health", "Health and care",
     r"(?:ma maladie|mon médecin|mon traitement|mon opération|"
     r"je suis handicapé\w*|mon handicap|mon assurance invalidité|mon ai\b|"
     r"je suis diabétique|mon cancer|ma santé)"),

    ("origin", "Origin and status",
     r"(?:je suis suisse|je suis étrang\w+|je suis (?:français|italien|"
     r"portugais|espagnol|allemand)\w*|j'ai la nationalité|mon permis "
     r"(?:b|c|de séjour)|je suis arrivé\w* en suisse|je suis naturalisé\w*|"
     r"j'ai immigré)"),

    ("mobility", "Transport",
     r"(?:ma voiture|mon vélo|mon scooter|ma moto|mon abonnement (?:général|"
     r"cff|demi-tarif)|je prends le train|je fais \d+ ?km|mon trajet)"),
)

# French negation, which is what flips a match. Matching the verb-adjacent
# forms alone ("ne suis pas") is not enough in a population that argues for a
# living: "De nous deux, ce n'est pas non plus moi qui ai comme associé de ma
# société ..." asserts the opposite of the company it appears to disclose, and
# the negation sits eight words from the match. So the particle itself is the
# signal, wherever it falls in the clause.
NEGATIONS = ("ne ", "n'", "aucun", "jamais", "pas moi qui", "sans être")

# ... but only within the SAME clause. "Je n'ai pas de voiture, mais je suis
# retraité" is a real disclosure whose run-up happens to contain a negation
# belonging to the clause before it, so the lookback stops at the nearest
# boundary rather than running on into the previous thought.
CLAUSE_BREAKS = (", mais ", " mais ", "; ", " car ", " donc ", " alors que ",
                 " tandis que ", " et ", " ou ", ". ", "! ", "? ")

# Conditional and hypothetical framings. Someone arguing "si j'étais patron"
# is not telling us they run a company.
HYPOTHETICALS = ("si j", "si je", "imaginez", "supposons", "admettons",
                 "quand je serai", "le jour où", "j'aimerais être",
                 "je voudrais être", "si vous", "à supposer")

_QUOTED = re.compile(r"«[^»]*»|\"[^\"]{3,}\"|“[^”]*”")
_SENTENCE = re.compile(r"[^.!?…]*[.!?…]|[^.!?…]+$")
# How far back to look for a negation or a hypothetical. A clause, not a
# paragraph: "si" three sentences earlier does not govern this one.
LOOKBACK = 70

MAX_QUOTE = 240
MAX_PER_CATEGORY = 6


def strip_quoted(text: str) -> str:
    """Blank out material in quotation marks.

    This population argues by quoting: the strongest writers open with `@nick`
    and hand the other person's words back to them. A disclosure inside those
    marks is usually not the writer's own, and attributing it to them is the
    one error this module must not make.
    """
    return _QUOTED.sub(lambda m: " " * len(m.group(0)), text)


def _is_disowned(haystack: str, at: int) -> bool:
    """Whether the run-up to a match negates it or makes it hypothetical.

    Only the clause the match sits in is consulted. A negation two clauses
    back governs its own verb, not this one, and reading further turns every
    "je n'ai pas de X, mais je suis Y" into a missed disclosure.
    """
    before = haystack[max(0, at - LOOKBACK):at]
    cut = max((before.rfind(b) + len(b) for b in CLAUSE_BREAKS
               if b in before), default=0)
    clause = before[cut:]
    return (any(n in clause for n in NEGATIONS)
            or any(h in clause for h in HYPOTHETICALS))


def sentence_around(text: str, at: int) -> str:
    """The sentence a match sits in, trimmed so the match stays inside it.

    The sentence rather than a fixed window, because a window cuts mid-clause
    and a truncated disclosure reads as a different claim than the whole one.

    Trimming a long sentence from its start is how a quote ends up not
    containing the phrase that produced it -- "ma voiture" can sit at
    character 200 of a sentence about fuel prices. So an over-long sentence is
    cut around the match instead, with an ellipsis where text was removed.
    """
    start, end = 0, len(text)
    for m in _SENTENCE.finditer(text):
        a, b = m.span()
        if a <= at < b:
            start, end = a, b
            break

    if end - start <= MAX_QUOTE:
        return text[start:end].strip()

    # Keep the match in view, with a third of the budget ahead of it.
    lead = MAX_QUOTE // 3
    lo = max(start, at - lead)
    hi = min(end, lo + MAX_QUOTE)
    out = text[lo:hi].strip()
    return ("…" if lo > start else "") + out + ("…" if hi < end else "")


def find(text: str) -> list[tuple[str, str]]:
    """(category, sentence) for every disclosure in one comment."""
    if not text:
        return []
    clean = strip_quoted(text)
    low = clean.lower()
    out = []
    for key, _label, pattern in CATEGORIES:
        for m in re.finditer(pattern, low):
            if _is_disowned(low, m.start()):
                continue
            out.append((key, sentence_around(clean, m.start())))
            break          # one hit per category per comment
    return out


def for_subject(comments: list[dict]) -> dict:
    """Every disclosure a subject made, grouped by dimension.

    Counts travel with the quotes because a prolific writer discloses more by
    writing more: `per_1000` is the rate that makes two subjects comparable,
    and `n_comments` says what it was measured on.
    """
    seen: dict[str, list[dict]] = defaultdict(list)
    tally: Counter = Counter()
    dupes: set[tuple[str, str]] = set()
    words = 0

    for c in comments:
        text = c.get("body_text") or ""
        words += text.count(" ") + 1
        for key, quote in find(text):
            tally[key] += 1
            fold = (key, " ".join(quote.lower().split())[:120])
            if fold in dupes:
                continue          # the same line re-posted, or re-scanned
            dupes.add(fold)
            if len(seen[key]) >= MAX_PER_CATEGORY:
                continue
            when = c.get("posted_at")
            seen[key].append({"quote": quote,
                              "when": when.date().isoformat() if when else None,
                              "journal": c.get("journal")})

    groups = []
    for key, label, _pat in CATEGORIES:
        if key not in tally:
            continue
        groups.append({"key": key, "label": label, "n": tally[key],
                       "quotes": seen[key]})
    return {"groups": groups, "n_disclosures": sum(tally.values()),
            "n_comments": len(comments),
            "per_1000": round(1000 * sum(tally.values()) / max(words, 1), 2)}


def for_nick(conn, *, nick: str | None = None, persona_id=None,
             community: str | None = None) -> dict:
    """Load one subject's comments and read their disclosures.

    Targeted on purpose. The first version went through
    `profiling.build_subjects`, which loads every comment in the corpus to
    answer a question about one person -- 2.8 million rows for a card in a
    sidebar. On the daemon that is worse than slow: the server is a
    single-threaded event loop, so the scan blocked it outright and new
    connections timed out during the handshake while one profile rendered.
    """
    from . import db

    label = nick
    if persona_id is not None:
        with conn.cursor() as cur:
            cur.execute("""SELECT p.label, array_agg(pa.nick)
                             FROM persona p
                             LEFT JOIN persona_alias pa ON pa.persona_id = p.id
                            WHERE p.id = %s GROUP BY p.label""", (int(persona_id),))
            row = cur.fetchone()
        if not row:
            return _empty(str(persona_id))
        label, nicks = row[0], [n for n in (row[1] or []) if n]
    else:
        if not nick:
            return _empty("")
        nicks = [nick]

    comments = db.comments_for_nicks(conn, nicks, limit=10 ** 9)
    if community:
        # These rows carry a journal, not a community, and the two are not the
        # same: 24 heures and the Tribune share one comment backend and so one
        # population. Seven nicknames exist in both communities and are
        # deliberately two different subjects, so the scoping has to go
        # through the journal table rather than compare slugs.
        with conn.cursor() as cur:
            cur.execute("SELECT slug, community FROM journal")
            of = dict(cur.fetchall())
        comments = [c for c in comments if of.get(c.get("journal")) == community]
    return {"label": label or (nicks[0] if nicks else ""),
            **for_subject(comments)}


def _empty(label: str) -> dict:
    return {"label": label, "groups": [], "n_disclosures": 0,
            "n_comments": 0, "per_1000": 0.0}


def main(argv=None) -> int:
    import argparse
    from . import db
    from .config import load_config
    p = argparse.ArgumentParser(prog="mediatracker.disclosures")
    p.add_argument("nick")
    p.add_argument("--community", default=None)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    conn = db.connect(load_config())
    if conn is None:
        raise SystemExit("no database")
    res = for_nick(conn, nick=a.nick, community=a.community)
    print(f"{res['label']}: {res['n_disclosures']} disclosures in "
          f"{res['n_comments']} comments ({res['per_1000']}/1000 words)")
    for g in res["groups"]:
        print(f"\n  {g['label']} ({g['n']})")
        for q in g["quotes"]:
            print(f"    [{q['when']}] {q['quote']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
