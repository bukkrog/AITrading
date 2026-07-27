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

# key -> display name, region (US/EU), IANA timezone, (open_h, open_m), (close_h, close_m)
EXCHANGES: dict[str, dict] = {
    "US": {"name": "US (NYSE/Nasdaq)", "region": "US", "tz": "America/New_York", "open": (9, 30), "close": (16, 0)},
    "CO": {"name": "OMX Copenhagen", "region": "EU", "tz": "Europe/Copenhagen", "open": (9, 0), "close": (17, 0)},
    "DE": {"name": "Xetra (DE)", "region": "EU", "tz": "Europe/Berlin", "open": (9, 0), "close": (17, 30)},
    "LSE": {"name": "London (LSE)", "region": "EU", "tz": "Europe/London", "open": (8, 0), "close": (16, 30)},
    "PAR": {"name": "Euronext (Paris/Amsterdam/Brussels)", "region": "EU", "tz": "Europe/Paris", "open": (9, 0), "close": (17, 30)},
    "MIL": {"name": "Borsa Italiana (Milan)", "region": "EU", "tz": "Europe/Rome", "open": (9, 0), "close": (17, 30)},
    "MAD": {"name": "BME (Madrid)", "region": "EU", "tz": "Europe/Madrid", "open": (9, 0), "close": (17, 30)},
    "STO": {"name": "Nasdaq Stockholm", "region": "EU", "tz": "Europe/Stockholm", "open": (9, 0), "close": (17, 30)},
    "OSL": {"name": "Oslo Børs", "region": "EU", "tz": "Europe/Oslo", "open": (9, 0), "close": (16, 20)},
    "HEL": {"name": "Nasdaq Helsinki", "region": "EU", "tz": "Europe/Helsinki", "open": (10, 0), "close": (18, 30)},
    "SWX": {"name": "SIX Swiss", "region": "EU", "tz": "Europe/Zurich", "open": (9, 0), "close": (17, 30)},
}

# Yahoo-style suffix -> exchange key.
_SUFFIX = {
    ".CO": "CO", ".DE": "DE", ".L": "LSE",
    ".PA": "PAR", ".AS": "PAR", ".BR": "PAR", ".LS": "PAR",
    ".MI": "MIL", ".MC": "MAD", ".ST": "STO", ".OL": "OSL", ".HE": "HEL",
    ".SW": "SWX", ".VX": "SWX", ".Z": "SWX",
}

# Saxo MIC-style exchange codes (after the colon in "AAPL:xnas") -> exchange key.
_MIC = {
    "xnas": "US", "xnys": "US", "arcx": "US", "xase": "US", "bats": "US", "iexg": "US",
    "xcse": "CO", "xetr": "DE", "xfra": "DE",
    "xlon": "LSE", "xpar": "PAR", "xams": "PAR", "xbru": "PAR", "xlis": "PAR",
    "xmil": "MIL", "xmad": "MAD", "xsto": "STO", "xosl": "OSL", "xhel": "HEL",
    "xswx": "SWX", "xvtx": "SWX",
}


def exchange_for_symbol(symbol: str) -> str:
    s = (symbol or "").strip()
    if ":" in s:  # Saxo MIC form, e.g. "AAPL:xnas"
        mic = s.split(":", 1)[1].lower()
        if mic in _MIC:
            return _MIC[mic]
    s = s.upper()
    for suffix, key in _SUFFIX.items():
        if s.endswith(suffix):
            return key
    return "US"  # plain tickers (the momentum universe) trade in the US


def exchange_label(symbol: str) -> str:
    """Human-readable exchange name for a symbol (Saxo MIC or Yahoo suffix)."""
    return EXCHANGES.get(exchange_for_symbol(symbol), EXCHANGES["US"])["name"]


def region_for_symbol(symbol: str) -> str:
    """Coarse region bucket: 'US' or 'EU'."""
    return EXCHANGES.get(exchange_for_symbol(symbol), EXCHANGES["US"]).get("region", "US")


# Exchange key -> the currency instruments on it trade in.
_EXCHANGE_CCY = {
    "US": "USD", "CO": "DKK", "DE": "EUR", "PAR": "EUR", "MIL": "EUR", "MAD": "EUR",
    "LSE": "GBP", "STO": "SEK", "OSL": "NOK", "HEL": "EUR", "SWX": "CHF",
}


def currency_for_symbol(symbol: str) -> str:
    """Best-effort trading currency for a symbol from its exchange (US→USD,
    :xcse→DKK, :xetr→EUR, …). Used to FX-convert sizing budgets, not for exact
    accounting."""
    return _EXCHANGE_CCY.get(exchange_for_symbol(symbol), "USD")


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


def is_open_or_soon(ex_key: str, within_minutes: int = 0) -> bool:
    """Open now, OR opening within ``within_minutes`` — lets discovery warm up
    the universe just before the bell so it's ready to trade at the open."""
    ex = EXCHANGES.get(ex_key, EXCHANGES["US"])
    now = _local_now(ex["tz"])
    if is_open(ex_key, now):
        return True
    if within_minutes <= 0:
        return False
    mins = (_next_open(ex_key, now) - now).total_seconds() / 60.0
    return 0 <= mins <= within_minutes


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
