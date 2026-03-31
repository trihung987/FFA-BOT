"""
Background task schedulers for check-in and lobby-division reminders.
"""

from __future__ import annotations

import re
from datetime import timedelta

import discord
from discord.ext import tasks, commands as ext_commands

from config import CHECKIN_CHANNEL_ID, DIVIDE_LOBBY_CHANNEL_ID
from entity import Match
from helpers import now_vn, format_vn_time


def _parse_duration(value: str) -> timedelta:
    """
    Convert a human-readable duration string to a :class:`timedelta`.

    Supported formats:
    - ``"1h"``  → 1 hour
    - ``"30p"`` → 30 minutes
    """
    match = re.fullmatch(r"(\d+)([hp])", value.strip().lower())
    if not match:
        raise ValueError(f"Unrecognised duration format: {value!r}")
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(minutes=amount)


def setup_scheduler(bot: ext_commands.Bot, db_session_factory):
    """
    Create and return the two periodic task loops:

    - ``match_scheduler`` – checks for upcoming check-in windows and posts reminders.
    - ``cleanup_scheduler`` – marks finished matches with an end_time.

    The caller is responsible for starting / stopping both loops.
    """

    @tasks.loop(minutes=1)
    async def match_scheduler() -> None:
        """Remind players when check-in or lobby-division time is reached."""
        now = now_vn()

        with db_session_factory() as session:
            pending = session.query(Match).filter(Match.end_time.is_(None)).all()

        for match in pending:
            time_start = match.time_start

            # --- Check-in reminder ---
            try:
                checkin_delta = _parse_duration(match.time_reach_checkin)
            except ValueError:
                continue
            checkin_time = time_start - checkin_delta
            if abs((now - checkin_time).total_seconds()) < 60:
                channel = bot.get_channel(CHECKIN_CHANNEL_ID)
                if channel:
                    await channel.send(
                        f"🔔 **Match #{match.id}** – Đã đến giờ check-in! "
                        f"Trận bắt đầu lúc {format_vn_time(time_start)}."
                    )

            # --- Lobby-division reminder ---
            try:
                divide_delta = _parse_duration(match.time_reach_divide_lobby)
            except ValueError:
                continue
            divide_time = time_start - divide_delta
            if abs((now - divide_time).total_seconds()) < 60:
                channel = bot.get_channel(DIVIDE_LOBBY_CHANNEL_ID)
                if channel:
                    await channel.send(
                        f"🔀 **Match #{match.id}** – Đã đến giờ chia lobby!"
                    )

    @tasks.loop(minutes=5)
    async def cleanup_scheduler() -> None:
        """Mark matches that have passed their start time as ended."""
        now = now_vn()

        with db_session_factory() as session:
            pending = (
                session.query(Match)
                .filter(Match.end_time.is_(None), Match.time_start < now)
                .all()
            )
            for match in pending:
                match.end_time = now
            session.commit()

    return match_scheduler, cleanup_scheduler
