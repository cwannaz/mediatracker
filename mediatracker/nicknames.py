"""What a handle is borrowed from.

A pseudonym is a choice, and the choice leaks. Somebody who signs themselves
after a 1970s children's bear, a Rimbaud pseudonym, a Latin legal maxim, a
Swiss soft drink or a Bowie persona is telling you which culture they expect to
be read in — the generation they belong to, the schooling they had, whether
their references are French, Swiss, anglophone or classical. That is a
sociological fact about the commenting public, and unlike everything in
`profiling` it is legible from the handle alone, before a single comment is
read.

**This says nothing about who the person is.** It records that a handle
alludes to Alain Delon, not that the commenter is an actor; that one quotes
Rabin, not that the writer is Israeli. Where a reference IS a public figure,
they are named here as a public figure — the thing being described is the
allusion, which is public by construction because the commenter published it.

**Handles that merely look like an ordinary personal name get no reading, on
purpose.** A great many here read as a plausible first name and surname. Some
will be invented, some may be the writer's own, and there is no way to tell
from the string — so classifying them would either assert an identity the
study refuses to record, or guess. Silence is the correct output.

Coverage is deliberately partial and hand-checked. An unrecognised handle
returns nothing rather than a guess: a column of confident nonsense would be
worse than an empty one, and the readings are meant to be argued with, which
is why each carries what it claims and how sure it is.
"""
from __future__ import annotations

import re
import unicodedata

# What kind of culture the handle draws on. The point of the axis is that these
# are not interchangeable: a Latin legal maxim and a Sesame Street puppet place
# a writer very differently.
DOMAINS = (
    "politics", "history", "literature", "cinema", "television", "music",
    "comics", "sport", "mythology", "religion", "science", "nature",
    "brand", "geography", "language", "internet",
)

# How the reference is used. "borrowed" is the plain case; the others are the
# interesting ones, because wordplay takes a reference the writer expects the
# reader to complete.
DEVICES = ("borrowed", "pun", "blend", "altered", "combined")


def _key(nick: str) -> str:
    """A handle reduced to what is stable across its respellings.

    Accents, case, spacing and punctuation all vary between a commenter's own
    variants — `oscar_the_grouch` and `oscarthegrouch` are one handle — so they
    are folded away. Digits are kept: `Sinalco65` and `Sinalco` are not
    obviously the same person, and a second lookup handles the suffix case.
    """
    folded = unicodedata.normalize("NFD", nick or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", folded.lower())


_TRAILING_DIGITS = re.compile(r"\d+$")


def _r(domain, refers_to, device="borrowed", note=None, confidence="high"):
    return {"domain": domain, "refers_to": refers_to, "device": device,
            "note": note, "confidence": confidence}


# Hand-checked, keyed by `_key`. Anything not here reads as unknown, which is
# the honest majority state.
LEXICON: dict[str, dict] = {
    # -- television and childhood, the strongest generational markers here
    "colargol": _r("television", "Colargol", note="the singing bear of the 1970s Franco-Polish series; places a childhood in that decade"),
    "oscarthegrouch": _r("television", "Oscar the Grouch", note="Sesame Street; the bin-dwelling grump, chosen as a posture"),
    "archiedbunker": _r("television", "Archie Bunker", device="altered", note="All in the Family; the reactionary the sitcom invited you to laugh at"),
    "barbatruk": _r("television", "Barbapapa", device="altered", note="from the barbatruc, the shape-shifting cry of the 1970s cartoon"),

    # -- music, cinema
    "thethinwhiteduke": _r("music", "David Bowie", note="Bowie's 1976 persona, not the man's name — a deep cut rather than a fan tag"),
    "wakemeup": _r("music", "Wake Me Up", confidence="low", note="a title shared by several songs; the allusion is unclear"),
    "alaindeloin": _r("cinema", "Alain Delon", device="pun", note="Delon crossed with 'de loin', from afar"),
    "lolamontes": _r("cinema", "Lola Montès", note="the 19th-century dancer, and Ophuls's 1955 film of her"),
    "nanook": _r("cinema", "Nanook of the North", note="Flaherty's 1922 documentary; also an Inuit given name", confidence="medium"),
    "ugostiglitz": _r("cinema", "Hugo Stiglitz", device="altered", confidence="medium", note="the Inglourious Basterds character, itself named after the Mexican actor"),
    "scar": _r("cinema", "Scar", confidence="low", note="the Lion King villain, if it is the reference at all"),
    "darkproutor": _r("cinema", "Darth Vader", device="pun", note="Dark Vador, the French name, crossed with 'prout'"),
    "thisissparta": _r("cinema", "300", note="the film's line, by way of Herodotus"),

    # -- comics
    "fluideglacial": _r("comics", "Fluide Glacial", note="Gotlib's satirical monthly, founded 1975; an adult comics culture, not a children's one"),

    # -- literature and the classical register
    "alcidebava": _r("literature", "Arthur Rimbaud", note="the pseudonym Rimbaud signed to 'Ce qu'on dit au poète a propos de fleurs'; recognising it takes more than school Rimbaud"),
    "rabelais": _r("literature", "François Rabelais"),
    "macbeth": _r("literature", "Macbeth", note="Shakespeare"),
    "lextalionis": _r("language", "lex talionis", note="the law of retaliation; Latin as legal vocabulary"),
    "nemoauditur": _r("language", "nemo auditur propriam turpitudinem allegans", note="a maxim of law, abbreviated as lawyers abbreviate it"),
    "nostravirus": _r("history", "Nostradamus", device="pun", note="the seer crossed with a virus, dated by the pandemic"),

    # -- politics and history
    "yitzhakrabin": _r("politics", "Yitzhak Rabin", note="the Israeli prime minister assassinated in 1995"),
    "melenchon": _r("politics", "Jean-Luc Mélenchon", note="French left; a French rather than Swiss political frame"),
    "snowdene": _r("politics", "Edward Snowden"),
    "charlesdarwin": _r("science", "Charles Darwin"),
    "vladimirillich": _r("politics", "Lenin", note="Vladimir Ilyich Ulyanov, by patronymic"),
    "vladimirillichoulianov": _r("politics", "Lenin", note="the full name, transliterated in French"),
    "wilhelmtell007": _r("history", "Wilhelm Tell", device="combined", note="the Swiss founding myth welded to James Bond — one national, one anglophone"),

    # -- brands, sport, places
    "sinalco65": _r("brand", "Sinalco", note="the soft drink, ubiquitous in Switzerland; the 65 reads as a birth year", confidence="medium"),
    "raidertwix": _r("brand", "Raider", note="the bar renamed Twix in 1991 — the joke only lands for those who remember both"),
    "n3tfl1xx1lft3n": _r("brand", "Netflix", device="altered", note="leetspeak, and a mirrored spelling"),
    "sauberf1": _r("sport", "Sauber", note="the Swiss Formula One team"),
    "veratti": _r("sport", "Marco Verratti", device="altered", confidence="medium", note="the footballer, one r short"),
    "atitlan": _r("geography", "Lake Atitlán", confidence="medium", note="Guatemala"),
    "krakatoa1886": _r("geography", "Krakatoa", note="the eruption was 1883; the date attached does not match it", confidence="medium"),
    "multicultibielbienne": _r("geography", "Biel/Bienne", device="blend", note="the bilingual city, prefixed multiculti — a claim about the place"),

    # -- mythology
    "icare4": _r("mythology", "Icarus", note="Icare, in French"),
    "mandragore41": _r("nature", "mandrake", note="the root of folklore and magic"),

    # -- language: idiom, slang, dialect, wordplay with no third party
    "lacarotteetlebaton": _r("language", "carrot and stick", note="the idiom, used whole as a signature"),
    "keepcalm": _r("language", "Keep Calm and Carry On", note="the British wartime poster, by way of its internet revival"),
    "dedioudediou": _r("language", "de Dieu de Dieu", note="a Franco-Provençal oath; Savoyard and Vaudois, and a strong regional tell"),
    "ceszigues": _r("language", "ces zigues", note="French slang for 'these blokes'"),
    "alainterieur": _r("language", "à l'intérieur", device="pun", note="the schoolyard name-pun: Alain Térieur"),
    "chacureuil": _r("nature", "chat + écureuil", device="blend", note="cat crossed with squirrel"),
    "stereotypo": _r("language", "stéréotype + typo", device="blend"),
    "fuossuoy": _r("language", "Youssouf", device="altered", confidence="medium", note="the name written backwards"),
    "thymthujano": _r("nature", "thym à thujanol", note="an essential-oil chemotype; a naturopathic vocabulary"),
}


# The lexicon indexed again with trailing numbers removed from BOTH sides, so
# a reference decorated with a year or an age still resolves: Sinalco65 and a
# hypothetical Sinalco12 allude to the same drink whether or not they are the
# same drinker. Ambiguous stems are dropped rather than resolved arbitrarily.
_STEMS: dict[str, dict] = {}
for _k, _v in LEXICON.items():
    _stem = _TRAILING_DIGITS.sub("", _k)
    if _stem and _stem != _k:
        _STEMS[_stem] = None if _stem in _STEMS and _STEMS[_stem] != _v else _v
_STEMS = {k: v for k, v in _STEMS.items() if v is not None and k not in LEXICON}


def read(nick: str | None) -> dict | None:
    """The reading for a handle, or None when we have none.

    Tries the folded handle, then the same with a trailing number removed — a
    year or an age bolted onto a reference is the commonest decoration here and
    should not hide the reference underneath.
    """
    if not nick:
        return None
    k = _key(nick)
    if k in LEXICON:
        return {**LEXICON[k], "matched": "exact"}
    # Either side may carry the decoration: the lexicon holds `mandragore41`
    # and the handle in front of us may be a bare `Mandragore`, or the reverse.
    stem = _TRAILING_DIGITS.sub("", k)
    hit = _STEMS.get(stem) or (LEXICON.get(stem) if stem != k else None)
    return {**hit, "matched": "stem"} if hit else None


def annotate(rows: list[dict], *, field: str = "nick",
             aliases: str | None = None, into: str = "reference") -> list[dict]:
    """Attach a reading to each row in place, under `into`.

    With `aliases` named, a row whose own label says nothing still gets the
    reading of one of its handles — a person clustered under a plain label may
    have been posting as Colargol all along, and the culture belongs to them
    either way. The first alias that resolves wins, and it says which one.
    """
    for r in rows:
        hit = read(r.get(field))
        if hit is None and aliases:
            for a in r.get(aliases) or []:
                hit = read(a)
                if hit:
                    hit = {**hit, "via": a}
                    break
        r[into] = hit
    return rows


def coverage(nicks) -> dict:
    """How much of a population the lexicon reaches, and in what proportions.

    Reported wherever the column is shown. A hand-built lexicon covers a small
    minority, and stating the fraction is what stops the column being read as a
    census of the commenting public's culture.
    """
    total = matched = 0
    domains: dict[str, int] = {}
    for n in nicks:
        total += 1
        r = read(n)
        if r:
            matched += 1
            domains[r["domain"]] = domains.get(r["domain"], 0) + 1
    return {"total": total, "matched": matched,
            "domains": dict(sorted(domains.items(), key=lambda kv: -kv[1]))}
