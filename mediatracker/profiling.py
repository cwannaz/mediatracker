"""Commenter profiling: build analysis subjects, measure their writing, and
export dossiers for the LLM pass.

A SUBJECT is one person's body of writing: a persona when its nicknames have
been linked, otherwise the bare nickname. Everything downstream analyses the
subject, never the individual pseudonym.

Two kinds of output are kept strictly apart:

  metrics  — computed here, deterministic and reproducible (counts, rates,
             rhythm of activity). These are measurements.
  language / gender / politics / philosophy / region / topics
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
    """One row per analysis subject with its full comment history."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COALESCE(pa.persona_id::text, c.author_nick)      AS key,
                   (pa.persona_id IS NOT NULL)                       AS is_persona,
                   COALESCE(p.label, c.author_nick)                  AS label,
                   c.author_nick, cs.posted_at, cs.body_text, cs.like_count,
                   j.slug AS journal, a.origin, art.headline
            FROM comment c
            JOIN comment_snapshot cs ON cs.comment_id = c.id
            JOIN article a  ON a.id = c.article_id
            JOIN journal j  ON j.id = a.journal_id
            LEFT JOIN persona_alias pa ON pa.nick = c.author_nick
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

    subjects: dict[str, dict] = {}
    for r in rows:
        s = subjects.setdefault(r["key"], {
            "key": r["key"],
            "kind": "persona" if r["is_persona"] else "nick",
            "label": r["label"],
            "aliases": set(),
            "comments": [],
        })
        s["aliases"].add(r["author_nick"])
        s["comments"].append(r)

    out = []
    for s in subjects.values():
        if len(s["comments"]) < min_comments:
            continue
        s["aliases"] = sorted(s["aliases"])
        out.append(s)
    out.sort(key=lambda s: -len(s["comments"]))
    return out


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

    # Accent discipline (see _ACCENTED_WORDS). `correct` = words written with
    # their accent; `missing` = the same words written bare. A writer with
    # correct == 0 simply does not type accents (habit, not error); one with
    # both is inconsistent, and each miss is a real mistake.
    correct_acc = sum(freq[w] for w in _ACC_FORMS if w in freq)
    missing_acc = sum(freq[b] for b in _BARE_FORMS if b in freq)
    if correct_acc == 0:
        accent_style, accent_consistency = "absent", None
    elif missing_acc == 0:
        accent_style, accent_consistency = "full", 1.0
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


def export(conn, min_comments: int, max_chars: int = 60000) -> list[dict]:
    os.makedirs(f"{OUT_DIR}/dossiers", exist_ok=True)
    subjects = build_subjects(conn, min_comments)
    manifest = []
    for i, s in enumerate(subjects):
        m = measure(s["comments"])
        sid = f"{'p' if s['kind']=='persona' else 'n'}_{_safe(s['label'])}_{i:04d}"
        lines = [
            f"SUBJECT: {s['label']}",
            f"kind: {s['kind']}   aliases: {', '.join(s['aliases'])}",
            f"comments: {m['n_comments']}   chars: {m['n_chars']}",
            "",
            "=== COMMENTS (chronological; verbatim, typos are intentional) ===",
        ]
        used = 0
        for c in s["comments"]:
            ts = str(c["posted_at"])[:16] if c["posted_at"] else "date unknown"
            head = f"\n[{ts}] as «{c['author_nick']}» on: {(c['headline'] or '')[:90]}"
            body = c["body_text"] or ""
            if used + len(body) > max_chars:
                lines.append(f"\n[… {m['n_comments'] - len(lines)} further comments omitted for length …]")
                break
            lines.append(head)
            lines.append(body)
            used += len(body)
        path = f"{OUT_DIR}/dossiers/{sid}.txt"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        manifest.append({
            "id": sid, "kind": s["kind"], "key": s["key"], "label": s["label"],
            "aliases": s["aliases"], "dossier": path,
            "n_comments": m["n_comments"], "n_chars": m["n_chars"],
            "first_seen": str(s["comments"][0]["posted_at"]),
            "last_seen": str(s["comments"][-1]["posted_at"]),
            "metrics": m,
        })
    with open(f"{OUT_DIR}/manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    return manifest


# --------------------------------------------------------------------------- #
# Ingest LLM output
# --------------------------------------------------------------------------- #

def ingest(conn, records: list[dict], manifest_by_id: dict) -> int:
    n = 0
    for rec in records:
        meta = manifest_by_id.get(rec.get("id"))
        if not meta:
            continue
        p = rec.get("profile") or {}
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO author_profile
                    (subject_kind, subject_key, label, n_comments, n_chars,
                     first_seen, last_seen, metrics, language, gender, politics,
                     philosophy, region, topics, notes, model, computed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                ON CONFLICT (subject_kind, subject_key) DO UPDATE SET
                    label=EXCLUDED.label, n_comments=EXCLUDED.n_comments,
                    n_chars=EXCLUDED.n_chars, first_seen=EXCLUDED.first_seen,
                    last_seen=EXCLUDED.last_seen, metrics=EXCLUDED.metrics,
                    language=EXCLUDED.language, gender=EXCLUDED.gender,
                    politics=EXCLUDED.politics, philosophy=EXCLUDED.philosophy,
                    region=EXCLUDED.region, topics=EXCLUDED.topics,
                    notes=EXCLUDED.notes, model=EXCLUDED.model, computed_at=now()
            """, (
                meta["kind"], meta["key"], meta["label"], meta["n_comments"],
                meta["n_chars"], meta["first_seen"], meta["last_seen"],
                db._jsonb(meta["metrics"]), db._jsonb(p.get("language")),
                db._jsonb(p.get("gender")), db._jsonb(p.get("politics")),
                db._jsonb(p.get("philosophy")), db._jsonb(p.get("region")),
                db._jsonb(p.get("topics")), p.get("notes"), rec.get("model"),
            ))
        n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mediatracker.profiling")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export"); e.add_argument("--min-comments", type=int, default=5)
    i = sub.add_parser("ingest"); i.add_argument("records")
    sub.add_parser("stats")
    args = ap.parse_args(argv)

    conn = db.connect(load_config())
    if conn is None:
        print("no database connection", file=sys.stderr)
        return 1
    db.ensure_schema(conn)

    if args.cmd == "export":
        man = export(conn, args.min_comments)
        chars = sum(m["n_chars"] for m in man)
        print(f"{len(man)} dossiers written to {OUT_DIR}/dossiers ({chars:,} chars)")
        print(f"manifest: {OUT_DIR}/manifest.json")
    elif args.cmd == "ingest":
        man = {m["id"]: m for m in json.load(open(f"{OUT_DIR}/manifest.json"))}
        data = json.load(open(args.records, encoding="utf-8"))
        recs = data if isinstance(data, list) else [data]
        print("profiles ingested:", ingest(conn, recs, man))
    else:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), count(*) FILTER (WHERE subject_kind='persona') FROM author_profile")
            print("profiles: %s (personas: %s)" % cur.fetchone())
    return 0


if __name__ == "__main__":
    sys.exit(main())
