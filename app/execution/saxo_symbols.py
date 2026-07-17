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

# Yahoo exchange suffix -> Saxo Symbol MIC (the ``:xxxx`` part of Saxo's Symbol).
SUFFIX_MIC: dict[str, str] = {
    ".CO": "xcse", ".DE": "xetr", ".PA": "xpar", ".AS": "xams", ".L": "xlon",
    ".MI": "xmil", ".MC": "xmce", ".ST": "xome", ".OL": "xosl", ".HE": "xhel",
    ".SW": "xvtx", ".BR": "xbru", ".LS": "xlis",
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
