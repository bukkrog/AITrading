"""Unit tests for v9: Saxo streaming frame parsing (hermetic — no network)."""
from __future__ import annotations

import json
import struct

from app.execution.saxo_streaming import _extract_price, _streaming_ws_url, parse_frames


def _frame(ref_id: str, payload: dict) -> bytes:
    """Build one Saxo streaming binary message envelope."""
    body = json.dumps(payload).encode("utf-8")
    ref = ref_id.encode("ascii")
    return (
        struct.pack("<Q", 1)          # msg id (8)
        + b"\x00\x00"                 # reserved (2)
        + bytes([len(ref)])           # ref-id size (1)
        + ref                         # ref id
        + b"\x00"                     # payload format = JSON
        + struct.pack("<I", len(body))  # payload size (4)
        + body
    )


def test_parse_single_frame():
    frame = _frame("prices", {"Uic": 211, "Quote": {"Mid": 333.26}})
    out = parse_frames(frame)
    assert len(out) == 1
    msg_id, ref_id, msg = out[0]
    assert msg_id == 1
    assert ref_id == "prices"
    assert msg["Uic"] == 211


def test_parse_multiple_concatenated_frames():
    data = _frame("prices", {"Uic": 211}) + _frame("_heartbeat", {}) + _frame("prices", {"Uic": 261})
    out = parse_frames(data)
    assert [r for _, r, _ in out] == ["prices", "_heartbeat", "prices"]


def test_extract_price_variants():
    assert _extract_price({"Quote": {"Mid": 100.0}}) == 100.0
    assert _extract_price({"Quote": {"Bid": 10.0, "Ask": 12.0}}) == 11.0
    assert _extract_price({"PriceInfoDetails": {"LastTraded": 50.0}}) == 50.0
    assert _extract_price({"Quote": {}}) is None
    assert _extract_price("not-a-dict") is None


def test_streaming_url_by_environment():
    assert "sim-streaming.saxobank.com" in _streaming_ws_url("sim")
    assert _streaming_ws_url("live").startswith("wss://streaming.saxobank.com")


# ---- European ticker -> Saxo listing mapping -----------------------------
def test_parse_yahoo_ticker():
    from app.execution.saxo_symbols import parse_yahoo_ticker

    assert parse_yahoo_ticker("NOVO-B.CO") == ("NOVO", "B", "xcse")
    assert parse_yahoo_ticker("BAYN.DE") == ("BAYN", None, "xetr")
    assert parse_yahoo_ticker("MC.PA") == ("MC", None, "xpar")
    assert parse_yahoo_ticker("AAPL") is None  # plain US ticker -> not EU-suffixed


def test_choose_by_mic_prefers_exchange_and_class():
    from app.execution.saxo_symbols import choose_by_mic

    matches = [
        {"Symbol": "NOV:xetr", "Identifier": 1},    # wrong exchange
        {"Symbol": "NOVOb:xcse", "Identifier": 2},  # right exchange + class B
        {"Symbol": "NOVOa:xcse", "Identifier": 3},  # right exchange, class A
    ]
    assert choose_by_mic(matches, "xcse", "B")["Identifier"] == 2
    assert choose_by_mic(matches, "xcse", None)["Identifier"] == 2  # first on-mic
    assert choose_by_mic(matches, "xpar", None) is None  # no listing on that mic


# ---- Real-time streaming exits -------------------------------------------
def test_streaming_exit_reason(monkeypatch):
    from app.config import settings
    from app.services import streaming_service as ss

    monkeypatch.setattr(settings, "stop_loss_pct", 0.08)
    monkeypatch.setattr(settings, "take_profit_pct", 0.15)
    monkeypatch.setattr(settings, "trailing_stop_pct", 0.10)

    # entry 100: stop at 92, take-profit at 115, trailing 10% off peak
    assert "stop-loss" in ss._exit_reason(100.0, 100.0, 91.0)
    assert "take-profit" in ss._exit_reason(100.0, 120.0, 116.0)
    # peak 130, price 113: below take-profit (115) but -13% from peak -> trailing.
    assert "trailing-stop" in ss._exit_reason(100.0, 130.0, 113.0)
    assert ss._exit_reason(100.0, 105.0, 104.0) is None  # inside all bands


def test_streaming_exit_off_when_thresholds_zero(monkeypatch):
    from app.config import settings
    from app.services import streaming_service as ss

    monkeypatch.setattr(settings, "stop_loss_pct", 0.0)
    monkeypatch.setattr(settings, "take_profit_pct", 0.0)
    monkeypatch.setattr(settings, "trailing_stop_pct", 0.0)
    assert ss._exit_reason(100.0, 200.0, 1.0) is None  # everything off -> never exit
