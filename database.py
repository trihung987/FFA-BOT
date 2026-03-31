"""
Database engine and session factory.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import DATABASE_URL
from entity import Base

engine = create_engine(DATABASE_URL, echo=False, future=True)

# Create all tables if they don't exist yet
Base.metadata.create_all(engine)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
