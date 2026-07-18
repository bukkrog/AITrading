"""Live activity feed — what the platform is doing RIGHT NOW.

Tiny in-memory ring buffer written by the automation loop / discovery /
execution, read by the dashboard ("Now: scanning 12 market sources…").
Deliberately not persisted: it describes the present, not history (that's
the audit log's job).
"""
from __future__ import annotations

import threading
import time
from collections import deque

_LOCK = threading.Lock()
_CURRENT: dict = {"text": "Idle", "ts": time.time()}
_RECENT: deque = deque(maxlen=12)


def set_activity(text: str) -> None:
    global _CURRENT
    with _LOCK:
        _RECENT.appendleft({"text": _CURRENT["text"], "ts": _CURRENT["ts"]})
        _CURRENT = {"text": text, "ts": time.time()}


def snapshot() -> dict:
    with _LOCK:
        now = time.time()
        return {
            "current": {"text": _CURRENT["text"], "seconds_ago": round(now - _CURRENT["ts"], 1)},
            "recent": [
                {"text": r["text"], "seconds_ago": round(now - r["ts"], 1)} for r in _RECENT
            ],
        }
