"""Database engine, session management, and SQLite WAL configuration."""

import os
from pathlib import Path
from typing import Any, Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.core.models import Base

# Default database location in the workspace
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "trading_automations.db"
DEFAULT_DB_URL = f"sqlite:///{DEFAULT_DB_PATH}"

DATABASE_DEBUG: dict[str, Any] = {
    "source": "default_sqlite",
    "secret_keys_found": [],
    "error": None,
}


def resolve_database_url() -> str:
    """Resolve database URL from Streamlit secrets, environment variables, or local default."""
    global DATABASE_DEBUG
    # 1. Check Streamlit Cloud st.secrets first
    try:
        import streamlit as st
        try:
            if hasattr(st, "secrets"):
                DATABASE_DEBUG["secret_keys_found"] = list(st.secrets.keys())
                # Check top-level keys case-insensitively
                for k in st.secrets:
                    val = st.secrets[k]
                    k_lower = str(k).lower()
                    if k_lower in ("database_url", "db_url", "postgres_url", "supabase_url", "supabase_db_url", "url"):
                        if isinstance(val, str) and val.strip():
                            DATABASE_DEBUG["source"] = f"st.secrets['{k}']"
                            return val.strip().strip("'").strip('"')
                    if isinstance(val, dict):
                        for sub_k, sub_v in val.items():
                            sub_k_lower = str(sub_k).lower()
                            if sub_k_lower in ("url", "database_url", "connection_string"):
                                if isinstance(sub_v, str) and sub_v.strip():
                                    DATABASE_DEBUG["source"] = f"st.secrets['{k}']['{sub_k}']"
                                    return sub_v.strip().strip("'").strip('"')
                            if isinstance(sub_v, str) and (sub_v.startswith("postgres://") or sub_v.startswith("postgresql://")):
                                DATABASE_DEBUG["source"] = f"st.secrets['{k}']['{sub_k}']"
                                return sub_v.strip().strip("'").strip('"')
                    # If any value anywhere in secrets is a PostgreSQL connection string
                    if isinstance(val, str) and (val.startswith("postgres://") or val.startswith("postgresql://")):
                        DATABASE_DEBUG["source"] = f"st.secrets['{k}']"
                        return val.strip().strip("'").strip('"')
        except Exception as e:
            DATABASE_DEBUG["error"] = f"st.secrets error: {e}"
    except ImportError:
        pass

    # 2. Check standard environment variables
    for env_key in ("DATABASE_URL", "database_url", "DB_URL", "POSTGRES_URL", "SUPABASE_DATABASE_URL"):
        env_val = os.getenv(env_key)
        if env_val and env_val.strip():
            DATABASE_DEBUG["source"] = f"os.environ['{env_key}']"
            return env_val.strip().strip("'").strip('"')

    # 3. Default to local SQLite
    DATABASE_DEBUG["source"] = "default_sqlite"
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
