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
    monkeypatch.setattr(settings, "discovery_sources", "")  # test the static-pool path
    monkeypatch.setattr(settings, "discovery_candidates", "AAA,BBB,CCC,DDD")
    picks = discovery.screen(session, top_n=3, refresh=True)
    assert 1 <= len(picks) <= 3
    # Scores are sorted descending.
    scores = [c.score for c in picks]
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= c.score <= 100 for c in picks)


def test_discovery_apply_sets_universe(session, monkeypatch):
    monkeypatch.setattr(settings, "market_data_source", "synthetic")
    monkeypatch.setattr(settings, "discovery_sources", "")  # test the static-pool path
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


def test_sector_cap_limits_concentration(monkeypatch):
    from app.services import universe

    sectors = {"A1": "Biotech", "A2": "Biotech", "A3": "Biotech", "A4": "Biotech",
               "T1": "Tech", "F1": "Finance", "U1": None}
    monkeypatch.setattr(universe, "_sector", lambda s: sectors.get(s))
    ranked = [{"symbol": s, "score": 100 - i} for i, s in
              enumerate(["A1", "A2", "A3", "A4", "T1", "F1", "U1"])]
    out = universe._apply_sector_cap(ranked, top_n=5, max_pct=0.30)
    syms = [r["symbol"] for r in out]
    # ceil(5*0.3)=2 biotech max; unknown sector exempt; next-best fill the rest.
    assert syms == ["A1", "A2", "T1", "F1", "U1"]
    # Disabled cap -> plain top-N.
    assert [r["symbol"] for r in universe._apply_sector_cap(ranked, 5, 0)] == \
        ["A1", "A2", "A3", "A4", "T1"]


def test_correlation_cap_skips_clones(monkeypatch):
    import numpy as np

    from app.services import universe

    monkeypatch.setattr(universe, "_sector", lambda s: None)  # isolate corr cap
    rng = np.random.default_rng(7)
    base = rng.normal(0, 0.02, 60)
    ranked = [
        {"symbol": "A", "score": 99, "returns": list(base)},
        {"symbol": "A2", "score": 98, "returns": list(base * 1.01)},          # clone of A
        {"symbol": "B", "score": 97, "returns": list(rng.normal(0, 0.02, 60))},  # independent
    ]
    out = universe._apply_sector_cap(ranked, top_n=2, max_pct=0, max_corr=0.7)
    assert [r["symbol"] for r in out] == ["A", "B"]  # clone A2 skipped
    # Cap disabled -> plain top-N.
    out2 = universe._apply_sector_cap(ranked, top_n=2, max_pct=0, max_corr=1.0)
    assert [r["symbol"] for r in out2] == ["A", "A2"]


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
