"""JSON request/response helpers for the websocket control surface.

Mirrors the algotrade component protocol: every request is a JSON object with a
"cmd" field; every response is a JSON object with "ok": true|false. Kept tiny and
dependency-free on purpose.
"""
from __future__ import annotations

import json
from typing import Any


class ProtocolError(ValueError):
    """Raised when an incoming frame is not a well-formed request."""


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str)


def parse_request(raw: str | bytes) -> dict[str, Any]:
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(msg, dict):
        raise ProtocolError("request must be a JSON object")
    if "cmd" not in msg or not isinstance(msg["cmd"], str):
        raise ProtocolError("request missing string 'cmd'")
    return msg


def ok(cmd: str, **fields: Any) -> str:
    return dumps({"ok": True, "cmd": cmd, **fields})


def error(cmd: str, message: str, **fields: Any) -> str:
    return dumps({"ok": False, "cmd": cmd, "error": message, **fields})
