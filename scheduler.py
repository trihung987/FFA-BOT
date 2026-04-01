"""
Background task schedulers for check-in and lobby-division reminders.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import tasks, commands as ext_commands

from config import CHECKIN_CHANNEL_ID, DIVIDE_LOBBY_CHANNEL_ID, REGISTER_CHANNEL_ID
from entity import Match, User
from helpers import now_vn, format_vn_time, parse_duration

log = logging.getLogger(__name__)


def setup_scheduler(bot: ext_commands.Bot, db_session_factory):
    """
    Create and return the three periodic task loops:

    - ``match_scheduler``         – checks for upcoming check-in windows and posts reminders.
    - ``cleanup_scheduler``       – marks finished matches with an end_time.
    - ``monthly_reset_scheduler`` – resets monthly_elo_gain for all users on the 1st of each month.

    The caller is responsible for starting / stopping all loops.
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
                checkin_delta = parse_duration(match.time_reach_checkin)
            except ValueError:
                continue
            checkin_time = time_start - checkin_delta
            if abs((now - checkin_time).total_seconds()) < 60:
                # 1. Disable the registration message so users can no longer join/cancel
                register_channel = bot.get_channel(REGISTER_CHANNEL_ID)
                if register_channel and match.register_message_id:
                    try:
                        reg_msg = await register_channel.fetch_message(match.register_message_id)
                        # Passing an empty View removes all action-row components from the message
                        await reg_msg.edit(view=discord.ui.View())
                    except discord.NotFound:
                        pass

                # 2. Send check-in embed to the check-in channel
                checkin_channel = bot.get_channel(CHECKIN_CHANNEL_ID)
                if checkin_channel:
                    from views import CheckInView, build_checkin_embed, _load_player_map

                    registered = match.register_users_id or []

                    # Build the initial check-in embed with current player names
                    with db_session_factory() as session:
                        db_match = session.get(Match, match.id)
                        if db_match is None:
                            continue
                        p_map = _load_player_map(session, registered)
                        embed = build_checkin_embed(db_match, p_map)

                    # Tag all registered players so they receive a notification
                    content = (
                        " ".join(f"<@{uid}>" for uid in registered) if registered else ""
                    )
                    view = CheckInView(match_id=match.id, db_session_factory=db_session_factory)
                    checkin_msg = await checkin_channel.send(
                        content=content, embed=embed, view=view
                    )

                    # 3. Persist the check-in message ID
                    with db_session_factory() as session:
                        db_match = session.get(Match, match.id)
                        if db_match is not None:
                            db_match.checkin_message_id = checkin_msg.id
                            session.commit()

            # --- Lobby-division time ---
            try:
                divide_delta = parse_duration(match.time_reach_divide_lobby)
            except ValueError:
                continue
            divide_time = time_start - divide_delta
            if abs((now - divide_time).total_seconds()) < 60:
                channel = bot.get_channel(DIVIDE_LOBBY_CHANNEL_ID)
                if channel:
                    await channel.send(
                        f"🔀 **Match #{match.id}** – Đã đến giờ chia lobby! "
                        "Đang xử lý…"
                    )
                # Run the full lobby-division pipeline
                from lobby_division import divide_lobbies
                import types

                # Reload the match inside a fresh session so all check-ins are visible
                snap = None
                with db_session_factory() as session:
                    fresh_match = session.get(Match, match.id)
                    if fresh_match is not None:
                        # Make a plain-namespace copy to avoid detached-instance issues
                        snap = types.SimpleNamespace(
                            id=fresh_match.id,
                            checkin_users_id=list(fresh_match.checkin_users_id or []),
                            count_fight=fresh_match.count_fight,
                            name_maps=list(fresh_match.name_maps or []),
                            time_start=fresh_match.time_start,
                            time_reach_checkin=fresh_match.time_reach_checkin,
                            time_reach_divide_lobby=fresh_match.time_reach_divide_lobby,
                        )
                if snap is not None:
                    try:
                        await divide_lobbies(bot, snap, db_session_factory)
                    except Exception as exc:
                        log.exception(
                            "divide_lobbies error for match #%s: %s", match.id, exc
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

    @tasks.loop(hours=1)
    async def monthly_reset_scheduler() -> None:
        """Reset monthly_elo_gain for all users once per calendar month.

        We track the last month that was reset in a closure variable so the
        reset fires exactly once even if the hourly tick happens to run at
        00:15 or 00:45 instead of exactly 00:00.
        """
        now = now_vn()
        current_month = (now.year, now.month)
        if now.day == 1 and current_month != monthly_reset_scheduler._last_reset_month:
            monthly_reset_scheduler._last_reset_month = current_month
            with db_session_factory() as session:
                session.query(User).update(
                    {User.monthly_elo_gain: 0}, synchronize_session=False
                )
                session.commit()

    # Initialise the tracking attribute before the loop is ever started
    monthly_reset_scheduler._last_reset_month = (None, None)

    return match_scheduler, cleanup_scheduler, monthly_reset_scheduler
