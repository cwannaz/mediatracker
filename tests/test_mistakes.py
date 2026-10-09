"""The error detectors, against real sentences from the corpus.

Every positive case below is a sentence the analysis pass flagged in a real
subject, and every negative is correct French that an earlier version of these
patterns would have called an error.
"""
from mediatracker import mistakes as mk


def _inf(text):
    return mk.participle_for_infinitive(text)


# -- the mistake the first version could not see -------------------------- #

def test_the_auxiliary_may_be_the_infinitive_avoir():
    # "d'avoir boycotter": the first detector required a CONJUGATED auxiliary
    # and scored this subject at zero.
    assert _inf("bravo aux ultras des 2 clubs d avoir boycotter!")
    assert _inf("Bravo aux joueurs d’avoir garder leur calme")
    assert _inf("tlmt de bravos pour avoir porter les couleurs suisses")


def test_adverbs_may_stand_between_the_auxiliary_and_the_verb():
    # "j'avais jamais penser": the first detector allowed nothing in between.
    assert _inf("J avais jamais penser qu on avait un si grand zoo")
    assert _inf("il a toujours voter oui")
    assert _inf("on a pas vraiment manger")


def test_a_conjugated_auxiliary_still_counts():
    assert _inf("C’est pour cela que j’avais voter oui.")
    assert _inf("ils ont decider de partir")


def test_object_pronouns_do_not_hide_it():
    assert _inf("il les a envoyer chez le docteur")
    assert _inf("je lui ai demander son avis")


# -- correct French that must NOT be flagged ------------------------------ #

def test_a_preposition_before_the_verb_licenses_the_infinitive():
    assert not _inf("il a commencé à travailler hier")
    assert not _inf("on a de quoi manger")
    assert not _inf("elle a pour habitude de manger tard")
    assert not _inf("il est pour rester ici")


def test_a_participle_between_them_licenses_the_infinitive():
    # Causative and perception verbs take an infinitive, correctly.
    assert not _inf("le patron a fait travailler ses employés")
    assert not _inf("j ai voulu manger plus tôt")
    assert not _inf("elle a laissé passer sa chance")
    assert not _inf("nous avons entendu chanter les oiseaux")


def test_adjectives_in_er_after_etre_are_not_verbs():
    for s in ("c est cher cette année", "il est fier de lui",
              "le dossier est entier", "il est premier au classement",
              "ce sac est léger", "mon voisin est étranger"):
        assert not _inf(s), s


def test_adverbs_in_er_are_not_verbs():
    # "c'est hyper bien": the first validation run flagged this one.
    assert not _inf("c’est hyper bien ce soleil")


def test_a_bare_a_after_a_participle_is_the_preposition_mistyped():
    # "Elle a reussi a montrer que ...": the writer dropped the accent on à,
    # so the infinitive is correct and this is a different error.
    assert not _inf("Elle a reussi a montrer que les vaudois sont fainéants")
    assert not _inf("il a continué a manger")
    # ... but a pronoun before the auxiliary is the real thing.
    assert _inf("nous a envoyer chez le docteur")
    assert _inf("il les a envoyer chez le docteur")


def test_nouns_in_er_are_not_verbs():
    assert not _inf("il y a danger pour les piétons")
    assert not _inf("c est le dernier hiver")


def test_a_correct_participle_is_not_a_hit():
    assert not _inf("il a mangé toute la tarte")
    assert not _inf("après avoir mangé il est parti")
    assert not _inf("les ultras ont boycotté le match")


def test_a_bare_infinitive_without_an_auxiliary_is_not_a_hit():
    assert not _inf("il va manger")
    assert not _inf("pour aller manger il faut sortir")
    assert not _inf("laisser tomber, ça sert à rien")


# -- the mirror error ----------------------------------------------------- #

def test_a_participle_agreed_under_avoir_is_caught():
    assert mk.agreed_after_avoir("les exportations n ont pas baissées en Suisse")
    assert mk.agreed_after_avoir("des problèmes qui ont retardés ce concert")


def test_agreement_required_by_a_preceding_object_is_not_an_error():
    # Here the rule DOES ask for agreement; flagging it would be wrong.
    assert not mk.agreed_after_avoir("les mesures qu il a prises hier")
    assert not mk.agreed_after_avoir("je les ai vues partir")


def test_etre_does_not_feed_the_avoir_rule():
    # "elles sont parties" is correct and belongs to être, not avoir.
    assert not mk.agreed_after_avoir("elles sont parties très tôt")


# -- the summary shape ---------------------------------------------------- #

def test_scan_reports_counts_rates_and_the_hits_behind_them():
    out = mk.scan("bravo d avoir boycotter! et les ventes ont baissées. "
                  "il a mangé.")
    assert out["participle_for_infinitive"] == 1
    assert out["agreed_after_avoir"] == 1
    assert out["per_1000_words"] > 0
    # A count nobody can check is not evidence: the hits come with it.
    assert "boycotter" in out["hits"]["participle_for_infinitive"][0]
    assert "baissées" in out["hits"]["agreed_after_avoir"][0]


def test_empty_text_is_not_an_error():
    assert mk.scan("")["participle_for_infinitive"] == 0
    assert mk.scan(None)["per_1000_words"] == 0.0


# -- wired into the metrics pass ------------------------------------------ #

def test_measure_reports_the_mistakes_it_can_count():
    """Every refresh carries the counts, so they exist for anyone profiled."""
    from datetime import datetime, timezone

    from mediatracker import profiling as pf

    comments = [
        {"body_text": "bravo aux ultras d avoir boycotter ce match!",
         "posted_at": datetime(2026, 9, 1, 10, tzinfo=timezone.utc)},
        {"body_text": "les exportations n ont pas baissées, merci Trump",
         "posted_at": datetime(2026, 9, 2, 11, tzinfo=timezone.utc)},
        {"body_text": "il a mangé puis il est parti, c est cher la vie",
         "posted_at": datetime(2026, 9, 3, 12, tzinfo=timezone.utc)},
    ]
    m = pf.measure(comments)["mistakes"]
    assert m["participle_for_infinitive"] == 1
    assert m["agreed_after_avoir"] == 1
    assert m["per_1000_words"] > 0
    assert "boycotter" in m["hits"]["participle_for_infinitive"][0]
    # The correct third comment contributes nothing.
    assert len(m["hits"]["agreed_after_avoir"]) == 1


def test_the_proximity_space_is_not_widened_by_this():
    """The new counts must not silently move every stored comparison.

    `proximity.FEATURES` is the z-space every score and the live calibration
    were computed in. Adding a measure to `measure()` is additive; adding one
    HERE changes what every past number meant, so it is a deliberate act and
    this test is what makes it deliberate.
    """
    from mediatracker import proximity as px

    assert "mistakes" not in px.FEATURES
    assert len(px.FEATURES) == 13
