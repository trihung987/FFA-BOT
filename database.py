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
    "ALTER TABLE matches ADD COLUMN divide_message_ids JSON DEFAULT '[]'",
    "ALTER TABLE lobbies ADD COLUMN display_message_ids JSON DEFAULT '[]'",
    # Create GIN index for JSONB queries on users_list
    "CREATE INDEX IF NOT EXISTS idx_lobby_users_list ON lobbies USING GIN (users_list jsonb_ops)",
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
