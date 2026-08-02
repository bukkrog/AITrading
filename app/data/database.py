"""Database engine / session management (SQLAlchemy 2.0).

Defaults to a local SQLite file so the platform runs with zero infrastructure.
Point ``DATABASE_URL`` at PostgreSQL/TimescaleDB for the production stack.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# SQLite needs ``check_same_thread=False`` when used across FastAPI threads.
# ``timeout`` is the busy-wait (seconds) before raising "database is locked" —
# the background automation loop and API requests write concurrently, so give
# a writer time to wait out an in-flight transaction instead of failing.
_is_sqlite = settings.database_url.startswith("sqlite")
_connect_args = (
    {"check_same_thread": False, "timeout": 30.0} if _is_sqlite else {}
)

engine = create_engine(
    settings.database_url,
    echo=False,
    future=True,
    connect_args=_connect_args,
)

if _is_sqlite:
    # WAL lets readers (dashboard/API) run concurrently with the single writer
    # (the tick loop) instead of blocking, and busy_timeout makes competing
    # writers wait rather than erroring out. synchronous=NORMAL is durable under
    # WAL and much faster. Applied to every pooled connection on connect.
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - infra glue
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Create all tables. Safe to call repeatedly (idempotent)."""
    # Import models so they are registered on ``Base.metadata``.
    from app.data import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    # Lightweight migrations: create_all never ALTERs existing tables, so add
    # late-added columns here (idempotent, SQLite + Postgres compatible).
    from sqlalchemy import inspect, text

    try:
        cols = {c["name"] for c in inspect(engine).get_columns("signals")}
        if "reject_reason" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE signals ADD COLUMN reject_reason VARCHAR(1024) DEFAULT ''"))
            logger.info("Migration: added signals.reject_reason")
    except Exception as exc:  # never block startup on a migration probe
        logger.warning("Column migration check failed: %s", exc)
    try:
        cols = {c["name"] for c in inspect(engine).get_columns("automation_state")}
        if "entry_mode" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE automation_state ADD COLUMN entry_mode VARCHAR(16) DEFAULT 'suggest'"))
            logger.info("Migration: added automation_state.entry_mode")
    except Exception as exc:  # never block startup on a migration probe
        logger.warning("entry_mode migration check failed: %s", exc)
    logger.info("Database initialised (%s)", settings.database_url)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session context manager."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
