"""The picture a commenter put on their account, and what it gives away.

The TX Group comment API hands us an avatar with every single comment, beside
the nickname, and this corpus discarded it for two years. The parser even said
so in passing: "only a nickname/avatar are exposed, no stable user id".

That reading was half right. There is no user id -- but an *uploaded* avatar is
very nearly one. Its filename is a hash, it is unique to the file, and it
belongs to the account rather than to the nickname, so it survives a rename.
That makes it the one thing in this corpus that can answer "who was this person
before?" with a fact instead of a stylometric shortlist.

Three populations, and confusing them would be the whole error:

  * **default** -- `anonym.<sha256>.png`, the placeholder every new account
    wears. Measured on a 2021 thread: 241 of 244 nicknames. It identifies
    nobody, and a query that treated it as a link would join half the corpus
    into one person.
  * **preset** -- `15@4x.<sha256>.png`, a small palette the site offers. Shared
    by many accounts, so it is weak evidence at best: on one current thread,
    nine of seventy-nine nicknames had picked the same one.
  * **uploaded** -- `media/static/profile/<32 hex>.png`, a file the account
    holder actually put there. Unique. This is the signal.

Adoption changed completely over the corpus: on a 2021 thread nearly everyone
still had the placeholder, while on a thread from November 2026 all 79
nicknames had chosen something.

What this module does NOT do is fetch the image. The filename already carries
the identity, and 180,000 nicknames' pictures is an afternoon of requests spent
to learn nothing the URL did not already say.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

# `media/static/profile/<32 hex>.png` -- a file this account uploaded.
_UPLOADED = re.compile(r"/media/static/profile/([0-9a-f]{8,})\.[a-z0-9]+$", re.I)
# `user/static/anonym.<sha256>.png` -- the placeholder.
_DEFAULT = re.compile(r"/user/static/(anonym\.[0-9a-f]{8,})\.[a-z0-9]+$", re.I)
# `user/static/15@4x.<sha256>.png` -- one of the site's own presets.
_PRESET = re.compile(r"/user/static/([^/]+@[0-9]+x\.[0-9a-f]{8,})\.[a-z0-9]+$", re.I)


def classify(url: str | None) -> tuple[str, str] | None:
    """(kind, key) for an avatar url, or None if it is not one.

    The key is the file's own identity with the extension dropped, so it
    changes when, and only when, the picture does. That is what makes a second
    row for the same nickname mean "they changed it" rather than "we looked
    again".
    """
    if not url or not isinstance(url, str):
        return None
    path = urlsplit(url).path
    if not path:
        return None
    m = _UPLOADED.search(path)
    if m:
        return "uploaded", m.group(1).lower()
    m = _DEFAULT.search(path)
    if m:
        return "default", m.group(1).lower()
    m = _PRESET.search(path)
    if m:
        return "preset", m.group(1).lower()
    # Something new: keep it under its own filename rather than dropping it.
    # A shape we have never seen is worth noticing, not worth guessing at.
    name = path.rsplit("/", 1)[-1]
    return ("other", name.rsplit(".", 1)[0].lower()) if name else None


def pairs_of(comments) -> dict[str, tuple[str, str, str]]:
    """One (kind, key, url) per nickname in a thread, not one per comment.

    A thread of 800 comments carries maybe 200 authors, each repeating the
    same avatar on every comment they wrote. Collapsing here is what keeps
    this to a couple of hundred upserts per article instead of a thousand.
    """
    out: dict[str, tuple[str, str, str]] = {}
    for c in comments:
        nick = getattr(c, "author_nick", None)
        url = getattr(c, "avatar_url", None)
        if not nick or not url:
            continue
        got = classify(url)
        if got:
            out[nick] = (got[0], got[1], url)
    return out


# --------------------------------------------------------------------------- #
# Asking the questions this table exists for
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    import argparse
    import json

    from . import db
    from .config import load_config

    p = argparse.ArgumentParser(prog="mediatracker.avatars")
    sub = p.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("history", help="every picture one nickname has worn")
    h.add_argument("nick")

    s = sub.add_parser("shared", help="every nickname that has worn one picture")
    s.add_argument("avatar_key")

    r = sub.add_parser("renames", help="uploaded pictures worn by several nicknames")
    r.add_argument("--limit", type=int, default=200)

    sub.add_parser("status", help="how much has been collected so far")

    a = p.parse_args(argv)
    conn = db.connect(load_config())
    if conn is None:
        print("Postgres unavailable")
        return 1

    if a.cmd == "history":
        rows = db.avatar_history(conn, a.nick)
        if not rows:
            print(f"no avatar recorded for {a.nick!r}")
            return 0
        for r_ in rows:
            print(f"  {r_['journal']:9s} {r_['kind']:9s} {r_['avatar_key'][:34]:34s} "
                  f"{r_['first_seen']:%Y-%m-%d} → {r_['last_seen']:%Y-%m-%d}")
    elif a.cmd == "shared":
        rows = db.avatar_sharers(conn, a.avatar_key)
        for r_ in rows:
            print(f"  {r_['author_nick'][:30]:30s} {r_['journal']:9s} {r_['kind']:9s} "
                  f"{r_['first_seen']:%Y-%m-%d} → {r_['last_seen']:%Y-%m-%d}")
        if len(rows) > 1 and rows[0]["kind"] == "uploaded":
            print(f"\n  {len(rows)} nicknames on one uploaded picture: "
                  "one account, renamed (or a shared login).")
    elif a.cmd == "renames":
        for r_ in db.avatar_successions(conn, limit=a.limit):
            print(f"  {r_['avatar_key'][:34]:34s} {r_['nicknames']} nicknames: "
                  f"{', '.join(r_['nicks'])}")
    else:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT kind, count(*) AS rows, count(DISTINCT author_nick) AS nicks,
                       count(DISTINCT avatar_key) AS pictures
                FROM author_avatar GROUP BY kind ORDER BY 2 DESC
            """)
            print(json.dumps(db._rows(cur), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
