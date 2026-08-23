# MediaTracker

Track, snapshot and reproduce online-journal articles and their comment threads
over time, for private local media research — and analyze commenters, including
linking pseudonyms across journals by writing style and content.

First tracked journals: **lematin.ch** and **24heures.ch** (both TX Group /
Tamedia titles). Data stays local; nothing is republished.

## What it does

- A daemon polls each journal once or twice a day, discovers new/updated
  articles, and records a **snapshot** whenever an article or comment changes —
  so you keep the full evolution, not just the latest state.
- Articles, comments, and images are stored in **PostgreSQL** (raw SQL, no ORM)
  with stable hash-based ids so re-seeing something is idempotent.
- Images are downloaded once into a **content-addressed blob store** on disk, so
  the local web app can reproduce each article (text + images + comments)
  offline, at any point in its history.
- A local **React + Vite** web app reproduces articles/threads and (later) shows
  the cross-journal author-linkage analysis.

## Architecture

```
mediatracker/            Python package (the daemon)
  server.py    asyncio websocket daemon + per-journal poll loops
  pipeline.py  journal-agnostic ingest: ids -> snapshots -> images
  sources/     one adapter per journal (lematin, 24heures, ...)
  db.py        psycopg3, self-migrating schema, graceful degradation
  store.py     JSONL fallback store when Postgres is down
  images.py    content-addressed blob store (stdlib format sniffing)
  fetch.py     polite stdlib-urllib fetching (per-host delay, robots)
  render.py    last-resort Playwright renderer for JS-only pages
webapp/        React 18 + Vite local GUI
config.toml    non-secret config; secrets via secret_postgre.env (outside repo)
mediatracker-journals.service  systemd --user unit (named to avoid AlgoTrade's own mediatracker daemon)
```

See `DOCTRINE.md` for conventions and `SCHEMA_SPEC.md` for the data model.

## Setup

```bash
# 1. Python deps
python3 -m pip install -r requirements-dev.txt
playwright install chromium          # only if a journal needs JS rendering

# 2. PostgreSQL (already installed locally)
createdb MediaTracker
# put credentials in a secret_postgre.env kept OUTSIDE the repo:
#   POSTGRE_USER=...
#   POSTGRE_PASSWORD=...
# (searched in repo root, ~/.config/mediatracker, ~/Documents/MATLAB)

# 3. Run the daemon (schema is created automatically on first boot)
python3 -m mediatracker --log-level INFO

# 4. Web app
cd webapp && npm install && npm run dev
```

## Control surface

The daemon speaks JSON over `ws://127.0.0.1:55030` (project port band 55000–55100):

| cmd          | effect                                          |
|--------------|-------------------------------------------------|
| `ping`       | liveness                                        |
| `health`     | self-verdict (loops, staleness, error counts)   |
| `status`     | degraded flag, journals, last ingest stats      |
| `ingest_now` | force an ingest (`{"cmd":"ingest_now","journal":"lematin"}`) |

## Status

Foundation complete and tested (ids, image store, config, JSONL store, daemon
skeleton). The `lematin` and `24heures` adapters are **stubs** pending live site
inspection (article markup + comment platform). Stylometry / nickname-linkage is
a later phase.

## Tests

```bash
python3 -m pytest -q
```
