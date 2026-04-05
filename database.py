"""
Database engine and session factory.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from config import DATABASE_URL
from entity import Base

engine = create_engine(DATABASE_URL, echo=False, future=True)

# Create all tables if they don't exist yet
Base.metadata.create_all(engine)

# ── Lightweight migrations ─────────────────────────────────────────────────────
# Add columns that may be missing from databases created before this migration.
_MIGRATIONS = [
    "ALTER TABLE matches ADD COLUMN status VARCHAR(20) DEFAULT 'open'",
]

for _sql in _MIGRATIONS:
    try:
        with engine.connect() as _conn:
            _conn.execute(text(_sql))
            _conn.commit()
    except Exception:
        # Column already exists or other benign DDL error – safe to ignore
        pass

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
