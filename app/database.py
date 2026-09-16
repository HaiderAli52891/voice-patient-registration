"""SQLAlchemy engine, session factory and FastAPI dependency."""

import os
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _build_engine():
    url = settings.database_url

    if settings.is_sqlite:
        # Make sure the parent directory exists before SQLite tries to open
        # the file, otherwise you get a confusing "unable to open database".
        path = url.split("sqlite:///")[-1]
        if path and path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        eng = create_engine(
            url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )

        # SQLite does not enforce foreign keys unless asked to.
        @event.listens_for(eng, "connect")
        def _fk_pragma(dbapi_conn, _record):  # pragma: no cover - driver glue
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return eng

    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db() -> None:
    """Create tables if they do not exist.

    Fine for a take-home. A real deployment would use Alembic migrations.
    """
    from app import models  # noqa: F401  (registers models on Base.metadata)

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
