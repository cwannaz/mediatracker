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

# 2. PostgreSQL (already installed locally). DB name is "MediaTracker-Journals"
#    (matches config.toml; quoted because of the hyphen/case).
sudo -u postgres psql -c 'CREATE ROLE mediatracker LOGIN PASSWORD :pw' \
  -v pw="'pick-a-strong-password'"
sudo -u postgres psql -c 'CREATE DATABASE "MediaTracker-Journals" OWNER mediatracker'
# put credentials in a secret_postgre.env kept OUTSIDE the repo:
#   POSTGRE_USER=mediatracker
#   POSTGRE_PASSWORD=pick-a-strong-password
# (searched in repo root, ~/.config/mediatracker, ~/Documents/MATLAB)

# 3. Run the daemon (schema is created automatically on first boot)
python3 -m mediatracker --log-level INFO

# 4. Web app
cd webapp && npm install && npm run dev
```

## Control surface

The daemon speaks JSON over `ws://127.0.0.1:55030` (project port band 55000–55100):

| cmd             | effect                                                        |
|-----------------|--------------------------------------------------------------|
| `ping`          | liveness                                                     |
| `health`        | self-verdict (loops, staleness, error counts)                |
| `status`        | degraded flag, journals, last scan stats                     |
| `list_sources`  | per-journal schedule, effective base URL, next scan, last run |
| `update_source` | save a journal's schedule (`{journal, schedule:{…}}`)        |
| `trigger_scan`  | queue a manual scan now (`{journal}`), no random offset      |
| `scan_status`   | current scan progress (done/total) + queue length            |
| `scan_history`  | recent `scan_run` rows (`{journal?, limit}`)                  |

## Scanning model

Each journal has a schedule (start-of-day + period + ±variability jitter,
timezone) stored in `journal.config` and edited from the GUI. A single-worker
queue runs scans one at a time (coincident schedules serialize); manual scans
skip the jitter. Each scan discovers homepage articles **and** re-scans
recently-seen ones (`active_rescan_days`), so comment/vote evolution keeps being
captured — including vote-distribution changes after commenting is disabled —
until an article disappears.

## GUI

`webapp/` (React + Vite) → top-level tabs; **Data Sources** tab has a vertical
rail of journals, each with a schedule editor, a manual **Scan now** button with
a live progress bar, and a recent-scans table. Article browser comes next.

## Status

Le Matin adapter complete (articles + images + threaded comments + vote
distributions + agency/source classification). 24 heures shares the adapter;
its comment `tenantId` is still pending. Scan engine + Data Sources GUI working.
Next: 24 heures comments, the article browser, and the profiling/stylometry phase
(per-nickname leaning, region, language mastery, probable gender).

## Tests

```bash
python3 -m pytest -q
```
