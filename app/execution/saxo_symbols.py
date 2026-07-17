"""Map Yahoo-suffixed European tickers to the right Saxo listing (v9c).

Discovery yields Yahoo tickers like ``NOVO-B.CO``, ``BAYN.DE``, ``MC.PA``. Saxo's
instrument search wants the *base* ticker and returns many listings across
exchanges; the right one is identified by the MIC in Saxo's ``Symbol`` field
(``NOVOb:xcse``, ``MC:xpar``). This module parses the Yahoo ticker and picks the
Saxo match on the matching exchange (verified against SIM: 24/26 EU names).

Class shares: Yahoo ``NOVO-B`` -> Saxo ``NOVOb`` — we search the base (``NOVO``)
and, among same-exchange matches, prefer the symbol carrying the class letter.
Searching with the class letter as a separate word matches junk (ticker "B"), so
we deliberately drop it from the keyword and use it only to disambiguate.
"""
from __future__ import annotations

import re

# Yahoo exchange suffix -> Saxo Symbol MIC (the ``:xxxx`` part of Saxo's Symbol).
SUFFIX_MIC: dict[str, str] = {
    ".CO": "xcse", ".DE": "xetr", ".PA": "xpar", ".AS": "xams", ".L": "xlon",
    ".MI": "xmil", ".MC": "xmce", ".ST": "xome", ".OL": "xosl", ".HE": "xhel",
    ".SW": "xvtx", ".BR": "xbru", ".LS": "xlis",
}

# Reverse: Saxo MIC -> Yahoo suffix (first mapping wins for shared MICs).
MIC_SUFFIX: dict[str, str] = {}
for _suf, _mic in SUFFIX_MIC.items():
    MIC_SUFFIX.setdefault(_mic, _suf)

# Names where the Yahoo base doesn't match Saxo's search keyword. Verified on SIM:
# Yahoo DHL.DE is Saxo "DPW:xetr" (found via "Deutsche Post"); INGA.AS is
# "ING:xams" (found via "ING"). Maps the full Yahoo ticker -> a search keyword.
KEYWORD_OVERRIDES: dict[str, str] = {
    "DHL.DE": "Deutsche Post",  # Saxo DPW:xetr
    "INGA.AS": "ING",           # Saxo ING:xams
    "MRK.DE": "Merck KGaA",     # Saxo MRCG:xetr (vs US Merck & Co on "MRK")
}


def parse_yahoo_ticker(symbol: str) -> tuple[str, str | None, str] | None:
    """Parse ``NOVO-B.CO`` -> ('NOVO', 'B', 'xcse'). None if not EU-suffixed."""
    u = (symbol or "").upper()
    for suffix, mic in SUFFIX_MIC.items():
        if u.endswith(suffix):
            base = symbol[: -len(suffix)]
            cls: str | None = None
            if "-" in base:
                base, cls = base.split("-", 1)
            return base, (cls or None), mic
    return None


def saxo_to_yahoo(saxo_symbol: str) -> str | None:
    """Reverse a Saxo Symbol to a Yahoo ticker, e.g. ``NOVOb:xcse`` -> ``NOVO-B.CO``.

    Saxo lowercases the class letter (``NOVOb``, ``CARLb``); we split a trailing
    lowercase letter off as the class and rebuild the Yahoo form. US listings
    (no mapped MIC) reduce to the bare base ticker. Best-effort.
    """
    if ":" not in (saxo_symbol or ""):
        return None
    local, mic = saxo_symbol.split(":", 1)
    suffix = MIC_SUFFIX.get(mic.lower())
    m = re.match(r"^([A-Za-z0-9]+?)([a-z])$", local)  # trailing lowercase = class
    if m and m.group(2):
        base, cls = m.group(1).upper(), m.group(2).upper()
        core = f"{base}-{cls}"
    else:
        core = local.upper()
    return f"{core}{suffix}" if suffix else core


def choose_by_mic(matches: list[dict], mic: str, cls: str | None) -> dict | None:
    """Pick the Saxo match listed on ``mic``, preferring the class share if given."""
    on_mic = [
        m for m in matches
        if str(m.get("Symbol", "")).lower().endswith(":" + mic)
    ]
    if not on_mic:
        return None
    if cls:
        pref = [
            m for m in on_mic
            if cls.lower() in str(m.get("Symbol", "")).split(":")[0].lower()
        ]
        if pref:
            on_mic = pref
    return on_mic[0]
