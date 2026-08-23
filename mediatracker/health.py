"""Minimal self-health tracker.

A local, dependency-free stand-in for algotrade's tradecore.daemonkit.Health
(this repo is independent and must not import from the algotrade tree). Each
async loop attaches itself and reports heartbeats; the daemon computes an
ok/warn/critical verdict about itself for the `health` websocket command.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class _LoopState:
    name: str
    last_beat: float | None = None
    runs: int = 0
    errors: int = 0


class Health:
    def __init__(self, service: str, *, monotonic=time.monotonic) -> None:
        self.service = service
        self._loops: dict[str, _LoopState] = {}
        self.warnings = 0
        self.errors = 0
        self._monotonic = monotonic
        self._started = monotonic()

    def attach_loop(self, name: str) -> None:
        self._loops.setdefault(name, _LoopState(name))

    def beat(self, name: str, *, ran: bool = True) -> None:
        st = self._loops.setdefault(name, _LoopState(name))
        st.last_beat = self._monotonic()
        if ran:
            st.runs += 1

    def loop_error(self, name: str) -> None:
        st = self._loops.setdefault(name, _LoopState(name))
        st.errors += 1
        self.errors += 1

    def warn(self) -> None:
        self.warnings += 1

    def verdict(self, *, stale_after_s: float = 3600.0) -> dict:
        now = self._monotonic()
        loops = {}
        status = "ok"
        for name, st in self._loops.items():
            age = None if st.last_beat is None else round(now - st.last_beat, 1)
            stale = age is not None and age > stale_after_s
            never = st.last_beat is None and (now - self._started) > stale_after_s
            if stale or never:
                status = "critical"
            loops[name] = {
                "runs": st.runs,
                "errors": st.errors,
                "last_beat_age_s": age,
                "stale": bool(stale or never),
            }
        if status == "ok" and self.errors:
            status = "warn"
        return {
            "service": self.service,
            "status": status,
            "uptime_s": round(now - self._started, 1),
            "warnings": self.warnings,
            "errors": self.errors,
            "loops": loops,
        }
