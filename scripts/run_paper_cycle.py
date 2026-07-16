"""Run one paper-trading cycle over the demo universe.

    python -m scripts.run_paper_cycle

Assumes ``scripts.seed_demo`` has been run first. Uses illustrative headlines
so the news gate is exercised; swap in real headlines / a news feed in v2.
"""
from __future__ import annotations

from app.data.database import init_db, session_scope
from app.logging_config import get_logger
from app.services import strategy_engine
from scripts.seed_demo import DEMO_UNIVERSE

logger = get_logger(__name__)

# Illustrative headlines — deterministic input for the news agent in MVP.
DEMO_HEADLINES = {
    "NOVO": ["Company beats earnings and raises full-year guidance", "Analyst upgrade on strong demand"],
    "MAERSK": ["Freight rates surge on robust global growth"],
    "ORSTED": ["Regulatory approval for major offshore expansion"],
    "DSV": ["Weak quarter as volumes decline; downgrade issued"],
    "CARLB": ["Record profit and dividend increase announced"],
    "GMAB": ["Lawsuit and probe weigh on shares"],
}


def main() -> None:
    init_db()
    with session_scope() as session:
        results = strategy_engine.run_cycle(
            session, DEMO_UNIVERSE, headlines_map=DEMO_HEADLINES
        )
    print("\n=== Paper cycle results ===")
    for r in results:
        flag = "APPROVED ✅" if r.approved else "rejected"
        print(
            f"{r.symbol:8s} {flag:12s} combined={r.combined_score:5.1f} "
            f"(Q={r.quant.score:.1f} N={r.news.score:.1f} R={r.risk.risk_score:.1f})"
        )
        for reason in r.reasons:
            print(f"    - {reason}")


if __name__ == "__main__":
    main()
