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
    # Lifecycle status: "open" → "checkin" → "dividing" | "cancelled"
    # nullable so that rows created before this column was added are treated as "open"
    status = Column(String(20), nullable=True, server_default="open")


class User(Base):
    """Represents a Discord user registered in the bot."""

    __tablename__ = "users"

    # Use the Discord snowflake (user ID) as the primary key
    id = Column(BigInteger, primary_key=True)
    # In-game name set by the player via /set_ingame_name
    ingame_name = Column(String(64), nullable=True)
    elo = Column(Integer, nullable=False, default=1000)
    ticket = Column(Integer, nullable=False, default=0)
    # ELO delta from the most recent change (positive = gained, negative = lost)
    last_elo_change = Column(Integer, nullable=False, default=0)
    # Cumulative ELO gains (positive only) within the current calendar month
    monthly_elo_gain = Column(Integer, nullable=False, default=0)
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
    # Tier name: "Huyền Thoại", "Chinh Phạt", "Kim Cương", "Tân Binh"
    tier = Column(String(50), nullable=False, server_default="")
    # Sequential number within the same tier for this match
    lobby_number = Column(Integer, nullable=False, server_default="1")
    # JSON list of Discord user IDs (ints) assigned to this lobby
    users_list = Column(JSON, nullable=False, default=lambda: [])
    # Number of AI slots added to fill the lobby up to 8
    ai_count = Column(Integer, nullable=False, server_default="0")
    # JSON dict: { str(user_id): [civ_fight1, civ_fight2, ...], "AI_1": [...] }
    civs = Column(JSON, nullable=False, default=lambda: {})
    # JSON dict: { "fight_1": { str(user_id): score, ... }, "fight_2": {...} }
    scores = Column(JSON, nullable=False, default=lambda: {})
    # "active" | "cancelled" | "finished"
    status = Column(String(20), nullable=False, server_default="active")
    # JSON list of voice channel IDs (one per fight)
    voice_channel_ids = Column(JSON, nullable=False, default=lambda: [])
    # JSON list of text channel IDs (one per fight)
    text_channel_ids = Column(JSON, nullable=False, default=lambda: [])
    # Discord message ID of the result-entry embed in the result channel
    result_message_id = Column(BigInteger, nullable=True)
