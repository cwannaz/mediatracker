"""JSONL graceful-degradation store.

When Postgres is unavailable, every record the daemon would have written is
appended here instead, one JSON object per line, so nothing observed is lost. A
later reconciliation pass can replay these files into Postgres.

Files are date-partitioned per record kind, e.g.:
    <jsonl_dir>/article_snapshot/2026-08-23.jsonl
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


class JsonlStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()

    def append(self, kind: str, record: dict) -> None:
        """Append one record under <root>/<kind>/<UTC-date>.jsonl."""
        now = datetime.now(timezone.utc)
        payload = {"_kind": kind, "_written_at": now.isoformat(), **record}
        day_dir = self.root / kind
        path = day_dir / f"{now:%Y-%m-%d}.jsonl"
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock:
            day_dir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
