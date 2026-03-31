from sqlalchemy import Column, Integer, BigInteger, String, JSON, DateTime
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


class Match(Base):
    """Represents a single FFA match event."""

    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # JSON list of Discord user IDs that registered
    register_users_id = Column(JSON, nullable=False, default=lambda: [])
    # JSON list of Discord user IDs that checked in
    checkin_users_id = Column(JSON, nullable=False, default=lambda: [])
    # JSON list of map names for each fight (length == count_fight)
    name_maps = Column(JSON, nullable=False, default=lambda: [])
    # Number of fights in this match
    count_fight = Column(Integer, nullable=False)
    # Scheduled start time
    time_start = Column(DateTime, nullable=False)
    # e.g. "1h" or "30p" – time before time_start when check-in opens
    time_reach_checkin = Column(String(20), nullable=False)
    # e.g. "30p" – time before time_start when lobbies are divided
    time_reach_divide_lobby = Column(String(20), nullable=False)
    # Time when the match ends
    end_time = Column(DateTime, nullable=True)
    # Discord message ID of the registration embed (set after the message is sent)
    register_message_id = Column(BigInteger, nullable=True)
    # Discord message ID of the check-in embed (set when check-in is triggered)
    checkin_message_id = Column(BigInteger, nullable=True)


class User(Base):
    """Represents a Discord user registered in the bot."""

    __tablename__ = "users"

    # Use the Discord snowflake (user ID) as the primary key
    id = Column(BigInteger, primary_key=True)
    # In-game name set by the player via /set_ingame_name
    ingame_name = Column(String(64), nullable=True)
    elo = Column(Integer, nullable=False, default=1000)
    ticket = Column(Integer, nullable=False, default=0)
    created_date = Column(DateTime, nullable=False, server_default=func.now())
    updated_date = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Lobby(Base):
    """Represents a lobby within a match."""

    __tablename__ = "lobbies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, nullable=False)
    # JSON list of Discord user IDs assigned to this lobby
    users_list = Column(JSON, nullable=False, default=lambda: [])
    # JSON dict: { "stage1": {"PlayerName": score, ...}, "stage2": {...}, ... }
    scores = Column(JSON, nullable=False, default=lambda: {})
