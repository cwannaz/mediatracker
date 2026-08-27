"""Commenter profiling: build analysis subjects, measure their writing, and
export dossiers for the LLM pass.

A SUBJECT is one person's body of writing: a persona when its nicknames have
been linked, otherwise the bare nickname. Everything downstream analyses the
subject, never the individual pseudonym.

Two kinds of output are kept strictly apart:

  metrics  — computed here, deterministic and reproducible (counts, rates,
             rhythm of activity). These are measurements.
  language / gender / politics / philosophy / region / topics / milieu
           — inferred by an LLM from the dossier, always with probabilities and
             supporting quotes. These are estimates.

Scope note: this profiles PSEUDONYMS. It is explicitly not an attempt to
identify the real people behind them.

Usage:
    python3 -m mediatracker.profiling export --min-comments 5   # write dossiers
    python3 -m mediatracker.profiling ingest profiles.json      # load LLM output
    python3 -m mediatracker.profiling stats
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import timezone
from zoneinfo import ZoneInfo

from . import db
from .config import load_config

OUT_DIR = "/mnt/storage/Projects/MediaTracker/profiling"
CH = ZoneInfo("Europe/Zurich")

WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)
SENT_RE = re.compile(r"[.!?…]+(?:\s|$)")

# Frequent French words whose correct spelling carries an accent. Seeing the
# BARE form is a missing accent; seeing the accented form proves the writer's
# keyboard can produce accents. The ratio between the two is what separates
# "never uses accents" (an input-method habit, not an error) from "uses accents
# but drops some" (a real mistake).
_ACCENTED_WORDS = """
été être même très après déjà où là voilà problème système différent différente
général générale première dernière année années élève élèves réussi arrêté
intérêt créer préfère régime vérité sécurité société économie énergie président
début débuts connaître paraître naître apparaître intérêts résumé données
problèmes systèmes matière misère caractère espère considère génère opère
député fédéral fédérale génération opération région média médias résultat
résultats député décision décisions référendum démocratie européen européenne
étranger étrangers écologie éducation église état états développement expérience
espèce espèces théorie stratégie côté écrit écrire lié liée pénal pénale
prévu prévue reçu reçue français française contrôle enquête intérêts complètement
évidemment également véritable spécial spéciale nécessaire précis précise
"""
# Bare forms that are themselves ordinary French words: counting them as a
# missing accent would flag every legitimate "ou" or "la". Such pairs are
# unusable for this measure and are dropped.
_AMBIGUOUS_BARE = {
    # each of these is itself a valid French word, so a bare occurrence proves
    # nothing about accents
    "ou", "la", "a", "cote", "cotes", "lie", "liee", "du", "sur", "tache",
    "mur", "pale", "cru", "sale", "pate", "jeune", "mais",
    # commonly written unaccented in French even by careful writers
    "media", "medias",
}
_ACC_PAIRS = []
for _w in _ACCENTED_WORDS.split():
    _bare = "".join(c for c in unicodedata.normalize("NFD", _w)
                    if not unicodedata.combining(c))
    if _bare != _w and _bare not in _AMBIGUOUS_BARE and len(_bare) >= 4:
        _ACC_PAIRS.append((_w, _bare))
_ACC_FORMS = {w for w, _ in _ACC_PAIRS}
_BARE_FORMS = {b for _, b in _ACC_PAIRS}


# --------------------------------------------------------------------------- #
# Subjects
# --------------------------------------------------------------------------- #

def build_subjects(conn, min_comments: int = 5) -> list[dict]:
    """One row per analysis subject with its full comment history.

    A subject is a nickname *within one comment community*, never a nickname on
    its own. "Marie03" on Le Matin and "Marie03" on the TX Romandie sites are
    two subjects until something proves they are one person: separate platforms
    mean separate registrations, and nothing links the two accounts. Titles that
    genuinely share a comment backend share a community, so a commenter there
    stays one subject however many of those titles they post on.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT j.community                                       AS community,
                   COALESCE(pa.persona_id::text, c.author_nick)      AS key,
                   (pa.persona_id IS NOT NULL)                       AS is_persona,
                   COALESCE(p.label, c.author_nick)                  AS label,
                   c.author_nick, cs.posted_at, cs.body_text, cs.like_count,
                   j.slug AS journal, a.origin, art.headline
            FROM comment c
            -- latest snapshot only: a comment re-seen by a later scan has one
            -- snapshot per scan, and joining them all would count it twice.
            JOIN LATERAL (
                SELECT posted_at, body_text, like_count
                FROM comment_snapshot
                WHERE comment_id = c.id ORDER BY fetched_at DESC LIMIT 1
            ) cs ON true
            JOIN article a  ON a.id = c.article_id
            JOIN journal j  ON j.id = a.journal_id
            -- An alias cluster holds inside one community only.
            LEFT JOIN persona_alias pa
                   ON pa.nick = c.author_nick AND pa.community = j.community
            LEFT JOIN persona p ON p.id = pa.persona_id
            LEFT JOIN LATERAL (
                SELECT headline FROM article_snapshot
                WHERE article_id = a.id ORDER BY fetched_at DESC LIMIT 1
            ) art ON true
            WHERE c.author_nick IS NOT NULL AND cs.body_text IS NOT NULL
            ORDER BY cs.posted_at NULLS LAST
        """)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    subjects: dict[tuple[str, str], dict] = {}
    for r in rows:
        s = subjects.setdefault((r["community"], r["key"]), {
            "community": r["community"],
            "key": r["key"],
            "kind": "persona" if r["is_persona"] else "nick",
            "label": r["label"],
            "aliases": set(),
            "journals": set(),
            "comments": [],
        })
        s["aliases"].add(r["author_nick"])
        s["journals"].add(r["journal"])
        s["comments"].append(r)

    out = []
    for s in subjects.values():
        s["comments"], s["n_duplicates"] = _dedupe(s["comments"])
        if len(s["comments"]) < min_comments:
            continue
        s["aliases"] = sorted(s["aliases"])
        # Which titles in the community this subject actually writes on — the
        # regional tell where the titles share a backend but not a local desk.
        s["journals"] = sorted(s["journals"])
        out.append(s)
    out.sort(key=lambda s: -len(s["comments"]))
    return out


def _fold_body(t: str) -> str:
    """Accent/case/punctuation-insensitive key for spotting the same comment
    captured twice."""
    t = unicodedata.normalize("NFKD", t or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t.lower())).strip()


def _dedupe(comments: list[dict]) -> tuple[list[dict], int]:
    """Drop the same text by the same subject captured more than once.

    The PDF archive holds several printings of the same article made years
    apart, so one comment can be stored under several article ids. Those are
    honest records of separate captures and stay in the database, but counting
    a comment twice would inflate every measure, so the analysis sees one copy
    — the earliest, which is closest to when it was written.
    """
    seen: dict[str, dict] = {}
    ordered: list[dict] = []
    dropped = 0
    for c in comments:
        key = _fold_body(c.get("body_text") or "")
        if len(key) < 20:          # too short to identify reliably; keep as is
            ordered.append(c)
            continue
        if key in seen:
            dropped += 1
            prev = seen[key]
            if (c.get("posted_at") and prev.get("posted_at")
                    and c["posted_at"] < prev["posted_at"]):
                prev.update(c)      # keep the earliest occurrence
            continue
        seen[key] = c
        ordered.append(c)
    ordered.sort(key=lambda c: (c.get("posted_at") is None, c.get("posted_at")))
    return ordered, dropped


# --------------------------------------------------------------------------- #
# Deterministic measures
# --------------------------------------------------------------------------- #

def measure(comments: list[dict]) -> dict:
    """Objective style and rhythm measures. No inference here."""
    texts = [c["body_text"] for c in comments if c.get("body_text")]
    words: list[str] = []
    sentences = chars = exclam = question = ellipsis = caps_words = 0
    typographic_apos = straight_apos = 0
    space_before_punct = 0

    for t in texts:
        chars += len(t)
        ws = WORD_RE.findall(t)
        words.extend(ws)
        sentences += len(SENT_RE.findall(t)) or 1
        exclam += t.count("!")
        question += t.count("?")
        ellipsis += t.count("…") + len(re.findall(r"\.\.\.", t))
        typographic_apos += t.count("’")
        straight_apos += t.count("'")
        # French typography puts a space before ! ? : ; — its presence or absence
        # is a stable habit of the writer.
        space_before_punct += len(re.findall(r"\s[!?;:]", t))
        for w in ws:
            if len(w) > 2 and w.isupper():
                caps_words += 1

    lower = [w.lower() for w in words]
    vocab = set(lower)
    freq = Counter(lower)
    hapax = sum(1 for w, n in freq.items() if n == 1)
    accented = sum(1 for w in words if any(unicodedata.combining(ch)
                   for ch in unicodedata.normalize("NFD", w)))

    # Accent discipline, judged one comment at a time (see _ACCENTED_WORDS).
    #
    # The unit matters. A comment with no accent anywhere in it is not evidence
    # of carelessness: people write from phones and from keyboards that cannot
    # produce them, and the same person will accent properly from another
    # machine an hour later. Aggregating the whole corpus turns that into a
    # false "inconsistent" — the writer is consistent within every comment, and
    # only the equipment changed between them.
    #
    # So a bare form counts as a mistake only where the same comment shows the
    # writer could have done otherwise: at least one accent appears in it. The
    # accent-free comments are counted separately rather than discarded,
    # because their share is itself a fact about how someone writes.
    correct_acc = missing_acc = 0
    unaccented_comments = accented_comments = 0
    for t in texts:
        ws = [w.lower() for w in WORD_RE.findall(t)]
        has_accent = any(unicodedata.combining(ch) for w in ws
                         for ch in unicodedata.normalize("NFD", w))
        if has_accent:
            accented_comments += 1
        else:
            unaccented_comments += 1
            continue                 # no accent to be inconsistent with
        f = Counter(ws)
        correct_acc += sum(f[w] for w in _ACC_FORMS if w in f)
        missing_acc += sum(f[b] for b in _BARE_FORMS if b in f)

    # Only ~100 word pairs are checkable, so silence is not proof. Saying
    # "full" because no bare form turned up would claim a writer accents
    # everything on the strength of words they never used.
    if accented_comments == 0:
        # Never an accent anywhere: equipment or habit, and nothing here can
        # tell which. Not an error either way.
        accent_style, accent_consistency = "absent", None
    elif correct_acc == 0 and missing_acc == 0:
        accent_style, accent_consistency = None, None
    elif missing_acc == 0:
        accent_style, accent_consistency = "full-in-sample", 1.0
    else:
        accent_style = "partial"
        accent_consistency = round(correct_acc / (correct_acc + missing_acc), 3)

    hours = Counter()
    dows = Counter()
    for c in comments:
        ts = c.get("posted_at")
        if ts:
            local = ts.astimezone(CH) if ts.tzinfo else ts.replace(tzinfo=timezone.utc).astimezone(CH)
            hours[local.hour] += 1
            dows[local.weekday()] += 1

    votes = [c["like_count"] for c in comments if c.get("like_count") is not None]
    n = max(1, len(texts))
    nw = max(1, len(words))

    return {
        "n_comments": len(comments),
        "n_chars": chars,
        "avg_words_per_comment": round(len(words) / n, 1),
        "avg_word_length": round(sum(len(w) for w in words) / nw, 2),
        "avg_sentence_length_words": round(len(words) / max(1, sentences), 1),
        "vocabulary_richness_ttr": round(len(vocab) / nw, 4),
        "hapax_ratio": round(hapax / max(1, len(vocab)), 4),
        "accented_word_rate": round(accented / nw, 4),
        # 'absent' = never types accents (habit, not an error);
        # 'partial' = types them inconsistently, so each omission IS an error;
        # 'full' = always accents the words that need it.
        "accent_style": accent_style,
        "accent_consistency": accent_consistency,
        "accent_correct_hits": correct_acc,
        "accent_missing_hits": missing_acc,
        # The share of comments written with no accent at all. High alongside a
        # clean `accent_consistency` is the signature of someone who accents
        # when the keyboard allows it, which is not the same writer as one who
        # accents erratically within a single comment.
        "unaccented_comment_share": round(unaccented_comments / n, 3),
        "accented_comments": accented_comments,
        "all_caps_word_rate": round(caps_words / nw, 4),
        "exclamations_per_comment": round(exclam / n, 2),
        "questions_per_comment": round(question / n, 2),
        "ellipsis_per_comment": round(ellipsis / n, 2),
        "apostrophe_typographic_share": round(
            typographic_apos / max(1, typographic_apos + straight_apos), 3),
        "space_before_punctuation_per_comment": round(space_before_punct / n, 2),
        "hour_histogram_ch": {str(h): hours.get(h, 0) for h in range(24)},
        "weekday_histogram": {str(d): dows.get(d, 0) for d in range(7)},
        "median_votes": sorted(votes)[len(votes) // 2] if votes else None,
        "top_words": [w for w, _ in freq.most_common(40) if len(w) > 4][:20],
    }


# --------------------------------------------------------------------------- #
# Dossiers for the LLM pass
# --------------------------------------------------------------------------- #

def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80]


def _fit_to_budget(comments: list[dict], max_chars: int) -> tuple[list[dict], bool]:
    """Choose which comments go in the dossier.

    Taking the first N characters would hand the reader only the opening months
    of a subject who wrote for a decade, which is exactly the case where drift
    over time matters most. So when the history does not fit, spread the sample
    evenly over the whole run and always keep both ends of the timeline.
    """
    total = sum(len(c.get("body_text") or "") for c in comments)
    if total <= max_chars:
        return comments, False

    n = len(comments)
    edge = min(15, n // 10)                     # anchor both ends of the period
    head, tail = comments[:edge], comments[n - edge:]
    middle = comments[edge:n - edge]
    budget = max_chars - sum(len(c.get("body_text") or "") for c in head + tail)

    picked: list[dict] = []
    if middle and budget > 0:
        stride = 1
        while stride <= len(middle):
            trial = middle[::stride]
            if sum(len(c.get("body_text") or "") for c in trial) <= budget:
                picked = trial
                break
            stride += 1
    return head + picked + tail, True


def manifest_path(community: str | None) -> str:
    """One manifest per community. Dossier ids embed the subject's index, so a
    community that is exported later must not renumber a community already
    profiled — keeping the manifests apart is what prevents that."""
    return f"{OUT_DIR}/manifest.json" if community is None \
        else f"{OUT_DIR}/manifest-{_safe(community)}.json"


def export(conn, min_comments: int, max_chars: int = 60000,
           community: str | None = None) -> list[dict]:
    os.makedirs(f"{OUT_DIR}/dossiers", exist_ok=True)
    subjects = build_subjects(conn, min_comments)
    if community is not None:
        subjects = [s for s in subjects if s["community"] == community]
    manifest = []
    for i, s in enumerate(subjects):
        m = measure(s["comments"])
        # The community is part of the id too: two platforms can each have a
        # "Taguenet", and they are two subjects with two dossiers.
        sid = (f"{'p' if s['kind']=='persona' else 'n'}"
               f"_{_safe(s['community'])}_{_safe(s['label'])}_{i:04d}")
        lines = [
            f"SUBJECT: {s['label']}",
            f"kind: {s['kind']}   aliases: {', '.join(s['aliases'])}",
            f"community: {s['community']}   writes on: {', '.join(s['journals'])}",
            f"comments: {m['n_comments']}   chars: {m['n_chars']}",
            "",
            "=== COMMENTS (chronological; verbatim, typos are intentional) ===",
        ]
        dated = [c["posted_at"] for c in s["comments"] if c["posted_at"]]
        span = (str(dated[0])[:10], str(dated[-1])[:10]) if dated else ("?", "?")
        kept, sampled = _fit_to_budget(s["comments"], max_chars)
        if sampled:
            lines.insert(4, (
                f"NOTE: this subject wrote too much to quote in full. The "
                f"{len(kept)} comments below are an EVEN SAMPLE spread across "
                f"the whole period ({m['n_comments']} in total), not the first "
                f"ones — so the span really is {span[0]} to {span[1]} and any "
                f"change over time is visible."))
        for c in kept:
            ts = str(c["posted_at"])[:16] if c["posted_at"] else "date unknown"
            head = f"\n[{ts}] as «{c['author_nick']}» on: {(c['headline'] or '')[:90]}"
            lines.append(head)
            lines.append(c["body_text"] or "")
        path = f"{OUT_DIR}/dossiers/{sid}.txt"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        manifest.append({
            "id": sid, "community": s["community"], "kind": s["kind"],
            "key": s["key"], "label": s["label"],
            "aliases": s["aliases"], "journals": s["journals"], "dossier": path,
            "n_comments": m["n_comments"], "n_chars": m["n_chars"],
            "n_duplicates_dropped": s.get("n_duplicates", 0),
            "dossier_sampled": sampled,
            "first_seen": str(dated[0]) if dated else None,
            "last_seen": str(dated[-1]) if dated else None,
            "metrics": m,
        })
    with open(manifest_path(community), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    return manifest


# --------------------------------------------------------------------------- #
# Ingest LLM output
# --------------------------------------------------------------------------- #

def _reconcile(p: dict, meta: dict) -> list[str]:
    """Bring one profile in line with the rules before it is stored.

    The reader of a dossier can misapply a label even when its judgement is
    sound, so two things are checked against ground truth rather than trusted:
    the accent label (the deterministic count knows whether accents appear at
    all) and the gender rule (no evidence must mean no claim).
    """
    warn: list[str] = []
    lang = p.get("language")
    if isinstance(lang, dict):
        measured = (meta.get("metrics") or {}).get("accent_style")
        stated = lang.get("accent_usage")
        # Only two measured verdicts are positive proof: 'partial' (the writer
        # accents some words and leaves others bare) and 'absent' (words
        # needing accents appear, always bare). 'full-in-sample' is the absence
        # of a counter-example among a hundred checkable words, which cannot
        # overrule a reading of the whole text.
        #
        # 'partial' additionally has to be a habit rather than a slip: a writer
        # who accents 40 words and misses one is careful, and relabelling them
        # would turn every later omission into a counted error on the strength
        # of a single typo.
        metrics = meta.get("metrics") or {}
        if measured == "partial":
            systematic = (metrics.get("accent_missing_hits", 0) >= 2
                          and (metrics.get("accent_consistency") or 1.0) <= 0.9)
            if not systematic:
                measured = None
        if measured in ("partial", "absent") and stated and measured != stated:
            lang["accent_usage"] = measured
            lang["accent_usage_stated"] = stated
            warn.append(f"{meta['id']}: accent_usage {stated!r} -> measured {measured!r}")

    g = p.get("gender")
    if isinstance(g, dict):
        male, female = float(g.get("male") or 0), float(g.get("female") or 0)
        if g.get("basis") in (None, "none") and (male >= 0.5 or female >= 0.5):
            # A confident read with no stated basis is exactly the inference
            # from topic or tone the contract forbids.
            g.update({"male": 0.0, "female": 0.0, "unknown": 1.0})
            warn.append(f"{meta['id']}: gender claim without evidence -> unknown")
        else:
            total = male + female + float(g.get("unknown") or 0)
            if total and abs(total - 1.0) > 0.02:
                g["male"], g["female"] = round(male / total, 3), round(female / total, 3)
                g["unknown"] = round(1.0 - g["male"] - g["female"], 3)
                warn.append(f"{meta['id']}: gender probabilities rescaled from {total:.2f}")
    return warn


def refresh_metrics(conn, *, min_comments: int = 5) -> dict:
    """Recompute the deterministic half of every stored profile, in place.

    `measure` changes when a measure is found to have been wrong — the accent
    rule became per-comment once it turned out that a comment with no accent
    anywhere is a keyboard, not a mistake — and the inferred half of a profile
    costs an LLM pass to rebuild. The two do not have to move together: this
    rewrites `metrics` and the counts and leaves every read of the text alone,
    so a corrected measure reaches `proximity` without a re-reading.

    Rows whose subject no longer meets `min_comments` are left as they are:
    deleting a profile is not a metrics update.
    """
    updated, skipped = 0, 0
    for s in build_subjects(conn, min_comments=min_comments):
        m = measure(s["comments"])
        stamps = [c["posted_at"] for c in s["comments"] if c.get("posted_at")]
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE author_profile
                   SET metrics = %s, n_comments = %s, n_chars = %s,
                       first_seen = %s, last_seen = %s
                 WHERE community = %s AND subject_kind = %s AND subject_key = %s
            """, (db._jsonb(m), m["n_comments"], m["n_chars"],
                  min(stamps) if stamps else None, max(stamps) if stamps else None,
                  s["community"], s["kind"], s["key"]))
            if cur.rowcount:
                updated += 1
            else:
                skipped += 1
    return {"updated": updated, "not_profiled": skipped}


def ingest(conn, records: list[dict], manifest_by_id: dict) -> tuple[int, list[str]]:
    n = 0
    warnings: list[str] = []
    missing: list[str] = []
    for rec in records:
        meta = manifest_by_id.get(rec.get("id"))
        if not meta:
            missing.append(str(rec.get("id")))
            continue
        p = rec.get("profile") or {}
        warnings.extend(_reconcile(p, meta))
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO author_profile
                    (community, subject_kind, subject_key, label, n_comments, n_chars,
                     first_seen, last_seen, metrics, language, gender, politics,
                     philosophy, region, topics, milieu, notes, model, computed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                ON CONFLICT (community, subject_kind, subject_key) DO UPDATE SET
                    label=EXCLUDED.label, n_comments=EXCLUDED.n_comments,
                    n_chars=EXCLUDED.n_chars, first_seen=EXCLUDED.first_seen,
                    last_seen=EXCLUDED.last_seen, metrics=EXCLUDED.metrics,
                    language=EXCLUDED.language, gender=EXCLUDED.gender,
                    politics=EXCLUDED.politics, philosophy=EXCLUDED.philosophy,
                    region=EXCLUDED.region, topics=EXCLUDED.topics,
                    milieu=EXCLUDED.milieu,
                    notes=EXCLUDED.notes, model=EXCLUDED.model, computed_at=now()
            """, (
                # Profiles written before communities existed were all Le Matin,
                # which is what the column defaults to; a manifest entry from
                # before that migration has no community of its own.
                meta.get("community", "lematin"),
                meta["kind"], meta["key"], meta["label"], meta["n_comments"],
                meta["n_chars"], meta["first_seen"], meta["last_seen"],
                db._jsonb(meta["metrics"]), db._jsonb(p.get("language")),
                db._jsonb(p.get("gender")), db._jsonb(p.get("politics")),
                db._jsonb(p.get("philosophy")), db._jsonb(p.get("region")),
                db._jsonb(p.get("topics")), db._jsonb(p.get("milieu")),
                p.get("notes"), rec.get("model"),
            ))
        n += 1
    if missing:
        warnings.append(f"{len(missing)} profile(s) had no manifest entry and were "
                        f"skipped: {', '.join(missing[:5])}"
                        + (" …" if len(missing) > 5 else ""))
    return n, warnings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mediatracker.profiling")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--min-comments", type=int, default=5)
    e.add_argument("--community", default=None,
                   help="export only this community's subjects, to its own manifest")
    i = sub.add_parser("ingest")
    i.add_argument("records")
    i.add_argument("--community", default=None,
                   help="the community whose manifest these records belong to")
    sub.add_parser("stats")
    args = ap.parse_args(argv)

    conn = db.connect(load_config())
    if conn is None:
        print("no database connection", file=sys.stderr)
        return 1
    db.ensure_schema(conn)

    if args.cmd == "export":
        man = export(conn, args.min_comments, community=args.community)
        chars = sum(m["n_chars"] for m in man)
        print(f"{len(man)} dossiers written to {OUT_DIR}/dossiers ({chars:,} chars)")
        print(f"manifest: {manifest_path(args.community)}")
    elif args.cmd == "ingest":
        man = {m["id"]: m for m in json.load(open(manifest_path(args.community)))}
        # Dossier ids carry the subject's position in the manifest, so the
        # manifest cannot be regenerated without renumbering profiles that are
        # already written. Refresh each entry's measurements in place instead,
        # matched on the subject itself, so the stored counts reflect the
        # de-duplicated history the profiles were judged on.
        fresh = {(s["community"], s["kind"], s["key"]): s
                 for s in build_subjects(conn, 1)}
        refreshed = 0
        for m in man.values():
            # A manifest written before communities existed carries none; those
            # subjects were all Le Matin, the only journal with data then.
            m.setdefault("community", "lematin")
            s = fresh.get((m["community"], m["kind"], m["key"]))
            if not s:
                continue
            dated = [c["posted_at"] for c in s["comments"] if c["posted_at"]]
            m["metrics"] = measure(s["comments"])
            m["n_comments"] = m["metrics"]["n_comments"]
            m["n_chars"] = m["metrics"]["n_chars"]
            m["first_seen"] = str(dated[0]) if dated else None
            m["last_seen"] = str(dated[-1]) if dated else None
            refreshed += 1
        print(f"measurements refreshed for {refreshed}/{len(man)} subjects")
        data = json.load(open(args.records, encoding="utf-8"))
        recs = data if isinstance(data, list) else [data]
        count, warns = ingest(conn, recs, man)
        print("profiles ingested:", count)
        for w in warns:
            print("  fixed:", w)
        if not warns:
            print("  no corrections needed")
    else:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), count(*) FILTER (WHERE subject_kind='persona') FROM author_profile")
            print("profiles: %s (personas: %s)" % cur.fetchone())
    return 0


if __name__ == "__main__":
    sys.exit(main())
