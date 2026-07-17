"""Unit tests for v4: feeds, discovery/screener, allocation, settings view."""
from __future__ import annotations

from app.config import settings
from app.data import feeds
from app.portfolio.engine import PortfolioEngine
from app.services import discovery


def test_feed_synthetic_bars(monkeypatch):
    monkeypatch.setattr(settings, "market_data_source", "synthetic")
    df = feeds.fetch_bars("NOVO", days=120)
    assert len(df) == 120
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_feed_news_empty_in_synthetic(monkeypatch):
    monkeypatch.setattr(settings, "market_data_source", "synthetic")
    assert feeds.fetch_news("NOVO") == []


def test_saxo_selftest_no_token(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "saxo_access_token", None)
    monkeypatch.setattr(settings, "saxo_environment", "sim")
    r = TestClient(app).get("/control/saxo-selftest").json()
    assert r["ok"] is False
    assert r["steps"][0]["name"] == "connect"
    assert r["steps"][0]["ok"] is False


def test_feed_saxo_returns_empty_without_token(monkeypatch):
    # Saxo source without a token must return NO data (never synthetic) — wrong
    # prices would corrupt sizing far worse than an empty result.
    monkeypatch.setattr(settings, "market_data_source", "saxo")
    monkeypatch.setattr(settings, "saxo_access_token", None)
    df = feeds.fetch_bars("AAPL", days=90)
    assert len(df) == 0


def test_discovery_ranks_candidates(session, monkeypatch):
    monkeypatch.setattr(settings, "market_data_source", "synthetic")
    monkeypatch.setattr(settings, "discovery_candidates", "AAA,BBB,CCC,DDD")
    picks = discovery.screen(session, top_n=3, refresh=True)
    assert 1 <= len(picks) <= 3
    # Scores are sorted descending.
    scores = [c.score for c in picks]
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= c.score <= 100 for c in picks)


def test_discovery_apply_sets_universe(session, monkeypatch):
    monkeypatch.setattr(settings, "market_data_source", "synthetic")
    monkeypatch.setattr(settings, "discovery_candidates", "AAA,BBB,CCC")
    from app.services import automation

    picks = discovery.apply_to_automation(session, top_n=2)
    assert len(picks) == 2
    state = automation.get_state(session)
    assert state.universe == ",".join(picks)


def test_universe_clean_filters_junk():
    from app.services import universe

    cleaned = universe._clean(["aapl", "MSFT", "BRK.B", "TOOLONGX", "", "123", "NVDA"])
    assert cleaned == ["AAPL", "MSFT", "NVDA"]  # class-dots, long, numeric dropped


def test_universe_gather_momentum_first_and_capped(monkeypatch):
    from app.services import universe

    monkeypatch.setitem(universe.SOURCES, "wsb", lambda: ["GME", "AMC", "TSLA"])
    monkeypatch.setitem(universe.SOURCES, "sp500", lambda: ["AAPL", "MSFT", "TSLA", "KO"])
    pool = universe.gather(["sp500", "wsb"], max_symbols=4)
    # momentum source (wsb) names come first; deduped; capped to 4.
    assert pool[:3] == ["GME", "AMC", "TSLA"]
    assert len(pool) == 4 and "AAPL" in pool


def test_universe_intl_clean_keeps_suffixed_tickers():
    from app.services import universe

    cleaned = universe._clean_intl(
        ["NOVO-B.CO", "MC.PA", "SAP.DE", "bad tick!", "MSFT", "waytoolongxxxx.CO"]
    )
    assert "NOVO-B.CO" in cleaned and "MC.PA" in cleaned and "SAP.DE" in cleaned
    assert "MSFT" in cleaned
    assert "bad tick!" not in cleaned and "waytoolongxxxx.CO" not in cleaned


def test_universe_european_sources_gather():
    from app.services import universe

    # OMX C25 is a static list (no network); gather must keep .CO suffixes.
    pool = universe.gather(["omxc25"], max_symbols=50)
    assert "NOVO-B.CO" in pool
    assert any(t.endswith(".CO") for t in pool)


def test_set_allocation(session):
    pf = PortfolioEngine(session)
    pf.set_allocation(50_000, reset_positions=True)
    assert pf.cash == 50_000
    assert pf.account.peak_value == 50_000
    assert pf.total_value({}) == 50_000


def test_set_allocation_rejects_nonpositive(session):
    pf = PortfolioEngine(session)
    import pytest

    with pytest.raises(ValueError):
        pf.set_allocation(0)
