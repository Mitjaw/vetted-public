"""
Database connections for the consumer app.
- consumer.db: SQLAlchemy ORM (read-write) — user/subscription state
- vetted.db:   raw sqlite3 read-only — owner data, never mutated by consumer code
"""
import os
import sqlite3
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONSUMER_DB_PATH = os.path.join(_BASE_DIR, "consumer.db")
CONSUMER_DB_URL  = f"sqlite:///{CONSUMER_DB_PATH}"

engine       = create_engine(CONSUMER_DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_consumer_db() -> Generator[Session, None, None]:
    """FastAPI dependency — yields a consumer.db SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# vetted.db lives one directory above consumer/
VETTED_DB_PATH = os.path.join(os.path.dirname(_BASE_DIR), "vetted.db")


def get_vetted_conn() -> sqlite3.Connection:
    """
    Return a read-only sqlite3 connection to vetted.db.
    Caller is responsible for closing it.
    ROI baseline: always closing price on video publish date — see db_manager.py.
    """
    conn = sqlite3.connect(f"file:{VETTED_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
