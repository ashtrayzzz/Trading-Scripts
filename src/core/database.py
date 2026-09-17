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


def resolve_database_url() -> str:
    """Resolve database URL from Streamlit secrets, environment variables, or local default."""
    # 1. Check Streamlit Cloud st.secrets first
    try:
        import streamlit as st
        try:
            if hasattr(st, "secrets"):
                if "DATABASE_URL" in st.secrets:
                    return str(st.secrets["DATABASE_URL"])
                elif "database" in st.secrets and "url" in st.secrets["database"]:
                    return str(st.secrets["database"]["url"])
        except Exception:
            pass
    except ImportError:
        pass

    # 2. Check standard environment variable
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return env_url

    # 3. Default to local SQLite
    return DEFAULT_DB_URL


DATABASE_URL = resolve_database_url()

# Normalize legacy postgres:// URI scheme to postgresql:// for SQLAlchemy
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Ensure parent directory exists for SQLite
if DATABASE_URL.startswith("sqlite"):
    db_file = DATABASE_URL.replace("sqlite:///", "")
    Path(db_file).parent.mkdir(parents=True, exist_ok=True)

# Engine configuration
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
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
