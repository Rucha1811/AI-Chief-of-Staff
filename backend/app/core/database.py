from os import environ
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

# An explicit DATABASE_URL env var wins over the settings snapshot so that
# tests (and any deployment) can point the app at a different database
# without restarting with a different settings file.
DATABASE_URL = environ.get("DATABASE_URL") or settings.DATABASE_URL

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

def _engine_kwargs() -> dict:
    """Bounded connection pool for Postgres so bursts of parallel ingest +
    background analysis don't exhaust the default 5+10 connections."""
    if DATABASE_URL.startswith("sqlite"):
        return {}
    return {
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
    }


engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    **_engine_kwargs(),
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
