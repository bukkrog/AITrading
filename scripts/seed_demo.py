"""Seed the database with a synthetic demo universe.

    python -m scripts.seed_demo

Deterministic — safe to re-run (existing bars are skipped).
"""
from __future__ import annotations

from app.data.database import init_db, session_scope
from app.logging_config import get_logger
from app.services import market_data_service

logger = get_logger(__name__)

DEMO_UNIVERSE = ["NOVO", "MAERSK", "ORSTED", "DSV", "CARLB", "GMAB"]


def main() -> None:
    init_db()
    with session_scope() as session:
        counts = market_data_service.seed_synthetic(session, DEMO_UNIVERSE, days=400)
    print("Seeded synthetic bars:")
    for sym, n in counts.items():
        print(f"  {sym}: {n} new bars")


if __name__ == "__main__":
    main()
