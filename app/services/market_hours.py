"""Exchange trading-hours helper (v8).

Determines whether the exchanges behind a set of symbols are open right now,
so automation can pause (and say so) when the market is closed.

Scope / honesty:
  * Regular cash-session hours only, in each exchange's local timezone.
  * Weekends are closed. **Public holidays and half-days are NOT modelled** —
    without a holiday calendar the market will look "open" on e.g. US holidays.
    A calendar library can be layered on later; this keeps zero extra deps.
  * Symbol → exchange is inferred from the ticker suffix (Yahoo style):
    ``.CO`` = Copenhagen, ``.DE`` = Xetra, ``.L`` = London; a plain ticker is
    assumed to be US (NYSE/Nasdaq), which matches the discovery universe.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# key -> display name, IANA timezone, (open_h, open_m), (close_h, close_m)
EXCHANGES: dict[str, dict] = {
    "US": {"name": "US (NYSE/Nasdaq)", "tz": "America/New_York", "open": (9, 30), "close": (16, 0)},
    "CO": {"name": "OMX Copenhagen", "tz": "Europe/Copenhagen", "open": (9, 0), "close": (17, 0)},
    "DE": {"name": "Xetra (DE)", "tz": "Europe/Berlin", "open": (9, 0), "close": (17, 30)},
    "LSE": {"name": "London (LSE)", "tz": "Europe/London", "open": (8, 0), "close": (16, 30)},
    "PAR": {"name": "Euronext (Paris/Amsterdam/Brussels)", "tz": "Europe/Paris", "open": (9, 0), "close": (17, 30)},
    "MIL": {"name": "Borsa Italiana (Milan)", "tz": "Europe/Rome", "open": (9, 0), "close": (17, 30)},
    "MAD": {"name": "BME (Madrid)", "tz": "Europe/Madrid", "open": (9, 0), "close": (17, 30)},
    "STO": {"name": "Nasdaq Stockholm", "tz": "Europe/Stockholm", "open": (9, 0), "close": (17, 30)},
    "OSL": {"name": "Oslo Børs", "tz": "Europe/Oslo", "open": (9, 0), "close": (16, 20)},
    "HEL": {"name": "Nasdaq Helsinki", "tz": "Europe/Helsinki", "open": (10, 0), "close": (18, 30)},
    "SWX": {"name": "SIX Swiss", "tz": "Europe/Zurich", "open": (9, 0), "close": (17, 30)},
}

# Yahoo-style suffix -> exchange key.
_SUFFIX = {
    ".CO": "CO", ".DE": "DE", ".L": "LSE",
    ".PA": "PAR", ".AS": "PAR", ".BR": "PAR", ".LS": "PAR",
    ".MI": "MIL", ".MC": "MAD", ".ST": "STO", ".OL": "OSL", ".HE": "HEL",
    ".SW": "SWX", ".VX": "SWX", ".Z": "SWX",
}


def exchange_for_symbol(symbol: str) -> str:
    s = (symbol or "").upper()
    for suffix, key in _SUFFIX.items():
        if s.endswith(suffix):
            return key
    return "US"  # plain tickers (the momentum universe) trade in the US


def _local_now(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def _at(day: datetime, hm: tuple[int, int]) -> datetime:
    return day.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)


def is_open(ex_key: str, now: datetime | None = None) -> bool:
    ex = EXCHANGES.get(ex_key, EXCHANGES["US"])
    now = now or _local_now(ex["tz"])
    if now.weekday() >= 5:  # Sat/Sun
        return False
    return _at(now, ex["open"]) <= now <= _at(now, ex["close"])


def _next_open(ex_key: str, now: datetime | None = None) -> datetime:
    ex = EXCHANGES.get(ex_key, EXCHANGES["US"])
    now = now or _local_now(ex["tz"])
    candidate = _at(now, ex["open"])
    if now >= candidate or now.weekday() >= 5:
        candidate = candidate + timedelta(days=1)
    while candidate.weekday() >= 5:  # skip weekend
        candidate = candidate + timedelta(days=1)
    return candidate


def exchange_status(ex_key: str) -> dict:
    ex = EXCHANGES.get(ex_key, EXCHANGES["US"])
    now = _local_now(ex["tz"])
    open_now = is_open(ex_key, now)
    nxt = None if open_now else _next_open(ex_key, now)
    return {
        "key": ex_key,
        "name": ex["name"],
        "open": open_now,
        "local_time": now.strftime("%H:%M %Z"),
        "hours": f"{ex['open'][0]:02d}:{ex['open'][1]:02d}–{ex['close'][0]:02d}:{ex['close'][1]:02d}",
        "next_open": nxt.isoformat() if nxt else None,
        "next_open_local": nxt.strftime("%a %H:%M %Z") if nxt else None,
    }


def status_for_symbols(symbols: list[str]) -> dict:
    """Aggregate open/closed status for the exchanges behind ``symbols``."""
    keys = sorted({exchange_for_symbol(s) for s in symbols}) or ["US"]
    exchanges = [exchange_status(k) for k in keys]
    return {
        "any_open": any(e["open"] for e in exchanges),
        "all_open": all(e["open"] for e in exchanges),
        "exchanges": exchanges,
    }
