"""Database engine, session management, and SQLite WAL configuration."""

import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.core.models import Base

# Default database location in the workspace
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "trading_automations.db"
DEFAULT_DB_URL = f"sqlite:///{DEFAULT_DB_PATH}"

DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DB_URL)

# Ensure parent directory exists for SQLite
if DATABASE_URL.startswith("sqlite"):
    db_file = DATABASE_URL.replace("sqlite:///", "")
    Path(db_file).parent.mkdir(parents=True, exist_ok=True)

# Engine configuration
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=False,
    future=True,
)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable WAL mode and foreign key enforcement on SQLite connections."""
    if DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Provide a transactional session context."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Initialize all tables defined in models."""
    Base.metadata.create_all(bind=engine)
