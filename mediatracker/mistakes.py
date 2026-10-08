"""Specific French mistakes, found locally and countably.

`profiling.measure` reports how a subject writes -- word lengths, accents,
punctuation. This reports what they get *wrong*, which is different evidence:
an error habit survives a change of subject matter, where vocabulary does not,
and a rare mistake made by two handles is worth more than a hundred shared
n-grams about football.

The analysis pass already names mistakes, but it costs a Claude call per
subject and reads only the subjects someone asked about. These detectors are
free and run over anyone, so a measure like "every handle that ever wrote this"
becomes answerable across the whole corpus.

Two error families, chosen because they are frequent in this corpus, mechanical
to detect, and nearly impossible to make on purpose:

* `participle_for_infinitive` -- "d'avoir **boycotter**" for *boycotté*. The
  auxiliary demands a participle and an infinitive arrives instead. French
  spells the two identically in speech (-er and -é are both /e/), so this is a
  writing habit, not a vocabulary gap.
* `agreed_after_avoir` -- "les exportations n'ont pas **baissées**". The
  opposite slip: a participle agreed with the subject although the auxiliary is
  *avoir*, which forbids it. Hypercorrection, and the same writers often do
  both.

The first version of this lived in a scratchpad and scored one of its own
subjects at zero: it required the auxiliary to be a conjugated form (so
"d'avoir boycotter" was invisible) and allowed nothing between auxiliary and
verb (so "j'avais jamais penser" was invisible too). Both are fixed here and
both are in the tests.
"""
from __future__ import annotations

import re

# Every form of avoir and être that can carry a participle, the infinitives
# included -- "après avoir manger" is the commonest shape of the error.
_AUX = (
    r"ai|as|a|avons|avez|ont|avais|avait|avions|aviez|avaient|"
    r"aurai|auras|aura|aurons|aurez|auront|aurais|aurait|aurions|auriez|auraient|"
    r"eu|avoir|aie|aies|ait|ayons|ayez|aient|"
    r"suis|es|est|sommes|etes|êtes|sont|etais|étais|etait|était|"
    r"etions|étions|etiez|étiez|etaient|étaient|"
    r"serai|seras|sera|serons|serez|seront|serais|serait|serions|seriez|seraient|"
    r"être|etre|soit|soient"
)
# What may stand between the auxiliary and the verb: adverbs, negation and
# object pronouns, and nothing else. A participle in that position
# ("a **fait** travailler", "a **voulu** manger") makes the following
# infinitive correct, so a closed list is what keeps those out.
_BETWEEN = (
    r"pas|plus|jamais|rien|point|guère|guere|bien|mal|tout|toute|toutes|tous|"
    r"déjà|deja|toujours|souvent|enfin|encore|vraiment|peut-être|peut-etre|"
    r"presque|trop|très|tres|aussi|même|meme|surtout|sûrement|surement|"
    r"probablement|certainement|finalement|y|en|le|la|les|lui|leur|me|m|te|t|"
    r"se|s|nous|vous"
)
# -er words that are not verbs. Without these, "c'est cher", "il est premier"
# and "elle est étrangère"'s masculine would all read as errors -- and after
# être, adjectives are exactly what is expected.
_NOT_VERBS = {
    "cher", "fier", "léger", "leger", "premier", "dernier", "entier",
    "familier", "régulier", "regulier", "singulier", "particulier", "amer",
    "étranger", "etranger", "danger", "hiver", "dîner", "diner", "déjeuner",
    "dejeuner", "goûter", "gouter", "souper", "panier", "quartier", "métier",
    "metier", "escalier", "clocher", "boulanger", "berger", "verger", "fer",
    "ver", "vers", "mer", "hier", "super", "plancher", "cahier", "papier",
    "pompier", "policier", "infirmier", "ouvrier", "banquier", "cuisinier",
    "jardinier", "prisonnier", "étrangers", "sommelier", "chevalier",
    "collier", "soulier", "tablier", "levier", "loyer", "foyer", "calendrier",
    "février", "fevrier", "janvier", "millier", "milliers", "pâtissier",
    "patissier", "epicier", "épicier", "mobilier", "immobilier", "nucléaire",
    "hyper", "inter", "outre",
}
# Pronouns, so that "nous a envoyer" is not mistaken for the case below.
_PRONOUNS = {"je", "j", "tu", "il", "elle", "on", "nous", "vous", "ils",
             "elles", "le", "la", "les", "lui", "leur", "me", "m", "te", "t",
             "se", "s", "y", "en", "qui", "que", "qu", "tout", "rien", "ça",
             "ca", "cela", "celui", "ceux", "quelqu"}
# A participle immediately before a bare "a" means that "a" is the preposition
# "à" typed without its accent -- "elle a reussi a montrer que" -- and the
# infinitive after it is correct. Measured on this corpus, that was one of
# only two false positives in the first validation run, and it is a mistake
# these writers make constantly, so it cannot be left in.
_PARTICIPLE_END = re.compile(r"(?:[ée]e?s?|i[est]?|u[est]?)$", re.IGNORECASE)
_WORD = r"[a-zà-öø-ÿ]"
# Auxiliary, then nothing but adverbs and pronouns, then the verb. Allowing
# only that closed list is what keeps correct French out: a preposition
# ("a de quoi manger", "est pour rester") or a participle ("a fait travailler")
# in that position licenses the infinitive, and neither is in the list, so
# those sentences simply do not match.
_PARTICIPLE_FOR_INF = re.compile(
    rf"\b(?P<aux>{_AUX})\b"
    rf"(?P<mid>(?:\s+(?:{_BETWEEN})\b)*)"
    rf"\s+(?P<verb>{_WORD}+er)\b",
    re.IGNORECASE)

# A participle agreed with the subject although the auxiliary is avoir. Only
# -é + e/s/es counts: -i and -u participles are spelt the same agreed or not in
# too many cases to separate mechanically.
_AGREED_AFTER_AVOIR = re.compile(
    rf"\b(?P<aux>ai|as|a|avons|avez|ont|avais|avait|avions|aviez|avaient|avoir)\b"
    rf"(?:\s+(?:{_BETWEEN})\b)*"
    rf"\s+(?P<verb>{_WORD}+[éè](?:es|e|s))\b",
    re.IGNORECASE)
# ... except where the object is a preceding relative or pronoun, which is when
# agreement IS required: "les mesures qu'il a prises", "il les a vues".
_OBJECT_BEFORE = re.compile(r"\b(?:qu['’]|que\s)|(?<!\w)(?:l['’]|les|la)\s+(?:"
                            r"ai|as|a|avons|avez|ont|avais|avait|avaient)\b",
                            re.IGNORECASE)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "")


def participle_for_infinitive(text: str) -> list[str]:
    """Hits of "auxiliary + infinitive-er" where a participle is required."""
    out = []
    clean = _clean(text)
    for m in _PARTICIPLE_FOR_INF.finditer(clean):
        if m.group("verb").lower() in _NOT_VERBS:
            continue
        if m.group("aux").lower() in ("a", "à"):
            before = clean[:m.start()].rstrip().split(" ")[-1].strip(",;:!?()")
            low = before.lower()
            if low and low not in _PRONOUNS and _PARTICIPLE_END.search(low):
                continue
        out.append(m.group(0).strip())
    return out


def agreed_after_avoir(text: str) -> list[str]:
    """Hits of a participle agreed with the subject under *avoir*."""
    out = []
    clean = _clean(text)
    for m in _AGREED_AFTER_AVOIR.finditer(clean):
        before = clean[max(0, m.start() - 24):m.start()]
        if _OBJECT_BEFORE.search(before + " " + m.group("aux")):
            continue            # a preceding object: agreement is correct here
        out.append(m.group(0).strip())
    return out


def scan(text: str) -> dict:
    """Both families, as counts and as the hits that produced them."""
    inf = participle_for_infinitive(text)
    agreed = agreed_after_avoir(text)
    words = len(re.findall(_WORD + "+", text or "")) or 1
    return {
        "participle_for_infinitive": len(inf),
        "agreed_after_avoir": len(agreed),
        "per_1000_words": round(1000 * (len(inf) + len(agreed)) / words, 2),
        "hits": {"participle_for_infinitive": inf[:25],
                 "agreed_after_avoir": agreed[:25]},
    }
