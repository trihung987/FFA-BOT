"""
Background task schedulers for check-in and lobby-division reminders.
"""

from __future__ import annotations

import logging
import types
from datetime import timedelta

import discord
from discord.ext import tasks, commands as ext_commands

from config import CHECKIN_CHANNEL_ID, DIVIDE_LOBBY_CHANNEL_ID, REGISTER_CHANNEL_ID, RESULT_CHANNEL_ID, MIN_PLAYERS_REQUIRED
from entity import Match, User
from helpers import now_vn, format_vn_time, parse_duration

log = logging.getLogger(__name__)


def setup_scheduler(bot: ext_commands.Bot, db_session_factory):
    """
    Create and return the four periodic task loops:

    - ``match_scheduler``          – checks for upcoming check-in windows and posts reminders.
    - ``cleanup_scheduler``        – marks finished matches with an end_time.
    - ``monthly_reset_scheduler``  – resets monthly_elo_gain for all users on the 1st of each month.
    - ``message_cleanup_scheduler`` – deletes bot messages for matches ended/cancelled 6+ hours ago.

    The caller is responsible for starting / stopping all loops.
    """

    @tasks.loop(minutes=1)
    async def match_scheduler() -> None:
        """Remind players when check-in or lobby-division time is reached."""
        now = now_vn()

        try:
            with db_session_factory() as session:
                pending = session.query(Match).filter(Match.end_time.is_(None)).all()
        except Exception as exc:
            log.exception("match_scheduler: DB error fetching pending matches")
            return

        for match in pending:
            time_start = match.time_start

            # --- Check-in reminder ---
            try:
                checkin_delta = parse_duration(match.time_reach_checkin)
            except ValueError:
                log.warning(
                    "match_scheduler: invalid time_reach_checkin %r for match #%s – skipping",
                    match.time_reach_checkin, match.id,
                )
                continue
            checkin_time = time_start - checkin_delta
            if abs((now - checkin_time).total_seconds()) < 60:
                # --- Atomic status guard: only one scheduler tick may claim check-in ---
                try:
                    with db_session_factory() as session:
                        claimed = (
                            session.query(Match)
                            .filter(
                                Match.id == match.id,
                                Match.status.in_([None, "open"]),
                            )
                            .update({"status": "checkin"}, synchronize_session=False)
                        )
                        session.commit()
                except Exception:
                    log.exception(
                        "match_scheduler: DB error claiming checkin for match #%s",
                        match.id,
                    )
                    continue

                if claimed == 0:
                    # Another tick already handled this check-in – skip entirely
                    log.debug(
                        "match_scheduler: check-in for match #%s already claimed – skipping",
                        match.id,
                    )
                    continue

                # Reload fresh match data after status update
                try:
                    with db_session_factory() as session:
                        db_match = session.get(Match, match.id)
                        if db_match is None:
                            log.warning(
                                "match_scheduler: match #%s disappeared after check-in claim",
                                match.id,
                            )
                            continue
                        registered = list(db_match.register_users_id or [])
                        reg_msg_id = db_match.register_message_id
                except Exception:
                    log.exception(
                        "match_scheduler: DB error reloading match #%s after check-in claim",
                        match.id,
                    )
                    continue

                register_channel = bot.get_channel(REGISTER_CHANNEL_ID)

                # --- Not enough players: cancel the match ---
                if len(registered) < MIN_PLAYERS_REQUIRED:
                    # Reply to the registration message with a cancellation notice
                    if register_channel and reg_msg_id:
                        try:
                            reg_msg = await register_channel.fetch_message(reg_msg_id)
                            await reg_msg.reply(
                                f"❌ **Match #{match.id} đã bị hủy** vì không đủ người đăng ký "
                                f"(**{len(registered)}/{MIN_PLAYERS_REQUIRED}** người tối thiểu)."
                            )
                            await reg_msg.edit(view=discord.ui.View())
                        except discord.NotFound:
                            log.debug(
                                "match_scheduler: register message %s for match #%s not found",
                                reg_msg_id, match.id,
                            )
                        except discord.HTTPException as exc:
                            log.error(
                                "match_scheduler: failed to notify cancellation for match #%s: %s",
                                match.id, exc,
                            )

                    # Mark match as cancelled
                    try:
                        with db_session_factory() as session:
                            db_match = session.get(Match, match.id)
                            if db_match is not None:
                                db_match.status = "cancelled"
                                db_match.end_time = now
                                session.commit()
                    except Exception:
                        log.exception(
                            "match_scheduler: DB error cancelling match #%s",
                            match.id,
                        )
                    continue

                # --- Enough players: start check-in ---

                # 1. Edit registration message: disable buttons + add "check-in started" notice
                if register_channel and reg_msg_id:
                    try:
                        from views import build_registration_embed, _load_player_map

                        reg_msg = await register_channel.fetch_message(reg_msg_id)
                        with db_session_factory() as session:
                            db_match = session.get(Match, match.id)
                            p_map = _load_player_map(session, registered)
                            reg_embed = build_registration_embed(db_match, p_map, checkin_started=True)
                        await reg_msg.edit(embed=reg_embed, view=discord.ui.View())
                    except discord.NotFound:
                        log.debug(
                            "match_scheduler: register message %s for match #%s not found",
                            reg_msg_id, match.id,
                        )
                    except discord.HTTPException as exc:
                        log.error(
                            "match_scheduler: failed to edit register message for match #%s: %s",
                            match.id, exc,
                        )
                elif not register_channel:
                    log.warning(
                        "match_scheduler: register channel %s not found (match #%s)",
                        REGISTER_CHANNEL_ID, match.id,
                    )

                # 2. Send check-in embed to the check-in channel
                checkin_channel = bot.get_channel(CHECKIN_CHANNEL_ID)
                if checkin_channel:
                    from views import CheckInView, build_checkin_embed, _load_player_map

                    # Build the initial check-in embed with current player names
                    try:
                        with db_session_factory() as session:
                            db_match = session.get(Match, match.id)
                            if db_match is None:
                                log.warning(
                                    "match_scheduler: match #%s disappeared before check-in message",
                                    match.id,
                                )
                                continue
                            p_map = _load_player_map(session, registered)
                            embed = build_checkin_embed(db_match, p_map)
                    except Exception:
                        log.exception(
                            "match_scheduler: DB error building check-in embed for match #%s",
                            match.id,
                        )
                        continue

                    # Tag all registered players so they receive a notification
                    content = (
                        " ".join(f"<@{uid}>" for uid in registered) if registered else ""
                    )
                    view = CheckInView(match_id=match.id, db_session_factory=db_session_factory)
                    try:
                        checkin_msg = await checkin_channel.send(
                            content=content, embed=embed, view=view
                        )
                    except discord.HTTPException:
                        log.exception(
                            "match_scheduler: failed to send check-in message for match #%s",
                            match.id,
                        )
                        continue

                    # 3. Persist the check-in message ID
                    try:
                        with db_session_factory() as session:
                            db_match = session.get(Match, match.id)
                            if db_match is not None:
                                db_match.checkin_message_id = checkin_msg.id
                                session.commit()
                    except Exception:
                        log.exception(
                            "match_scheduler: DB error saving checkin_message_id for match #%s",
                            match.id,
                        )
                else:
                    log.warning(
                        "match_scheduler: check-in channel %s not found (match #%s)",
                        CHECKIN_CHANNEL_ID, match.id,
                    )

            # --- Lobby-division time ---
            try:
                divide_delta = parse_duration(match.time_reach_divide_lobby)
            except ValueError:
                log.warning(
                    "match_scheduler: invalid time_reach_divide_lobby %r for match #%s – skipping",
                    match.time_reach_divide_lobby, match.id,
                )
                continue
            divide_time = time_start - divide_delta
            if abs((now - divide_time).total_seconds()) < 60:
                # --- Atomic status guard: only process lobby division when check-in is done ---
                try:
                    with db_session_factory() as session:
                        claimed = (
                            session.query(Match)
                            .filter(
                                Match.id == match.id,
                                Match.status == "checkin",
                            )
                            .update({"status": "dividing"}, synchronize_session=False)
                        )
                        session.commit()
                except Exception:
                    log.exception(
                        "match_scheduler: DB error claiming divide for match #%s",
                        match.id,
                    )
                    continue

                if claimed == 0:
                    log.debug(
                        "match_scheduler: divide for match #%s already claimed or wrong state – skipping",
                        match.id,
                    )
                    continue

                # Reload the match inside a fresh session so all check-ins are visible
                snap = None
                try:
                    with db_session_factory() as session:
                        fresh_match = session.get(Match, match.id)
                        if fresh_match is not None:
                            snap = types.SimpleNamespace(
                                id=fresh_match.id,
                                checkin_users_id=list(fresh_match.checkin_users_id or []),
                                checkin_message_id=fresh_match.checkin_message_id,
                                register_users_id=list(fresh_match.register_users_id or []),
                                count_fight=fresh_match.count_fight,
                                name_maps=list(fresh_match.name_maps or []),
                                time_start=fresh_match.time_start,
                                time_reach_checkin=fresh_match.time_reach_checkin,
                                time_reach_divide_lobby=fresh_match.time_reach_divide_lobby,
                            )
                        else:
                            log.warning(
                                "match_scheduler: match #%s not found when building snap for divide",
                                match.id,
                            )
                except Exception:
                    log.exception(
                        "match_scheduler: DB error building snap for divide (match #%s)",
                        match.id,
                    )

                if snap is None:
                    continue

                # 1. Edit check-in message: disable button + show "ended" notice
                checkin_channel = bot.get_channel(CHECKIN_CHANNEL_ID)
                if checkin_channel and snap.checkin_message_id:
                    try:
                        from views import build_checkin_embed, _load_player_map

                        checkin_msg_obj = await checkin_channel.fetch_message(snap.checkin_message_id)
                        with db_session_factory() as session:
                            db_match = session.get(Match, snap.id)
                            all_ids = list(
                                set((db_match.register_users_id or []) + (db_match.checkin_users_id or []))
                            ) if db_match else []
                            p_map = _load_player_map(session, all_ids)
                            ended_embed = build_checkin_embed(db_match, p_map, ended=True)
                        await checkin_msg_obj.edit(embed=ended_embed, view=discord.ui.View())
                    except discord.NotFound:
                        log.debug(
                            "match_scheduler: check-in message %s for match #%s not found",
                            snap.checkin_message_id, snap.id,
                        )
                    except discord.HTTPException as exc:
                        log.error(
                            "match_scheduler: failed to update check-in message for match #%s: %s",
                            snap.id, exc,
                        )

                # 2. Send announcement to divide-lobby channel
                channel = bot.get_channel(DIVIDE_LOBBY_CHANNEL_ID)
                if channel:
                    try:
                        await channel.send(
                            f"🔀 **Match #{match.id}** – Đã đến giờ chia lobby! "
                            "Đang xử lý…"
                        )
                    except discord.HTTPException as exc:
                        log.error(
                            "match_scheduler: failed to send divide announcement for match #%s: %s",
                            match.id, exc,
                        )
                else:
                    log.warning(
                        "match_scheduler: divide-lobby channel %s not found (match #%s)",
                        DIVIDE_LOBBY_CHANNEL_ID, match.id,
                    )

                # 3. Run the full lobby-division pipeline
                from lobby_division import divide_lobbies
                try:
                    await divide_lobbies(bot, snap, db_session_factory)
                except Exception:
                    log.exception(
                        "divide_lobbies error for match #%s", match.id
                    )

    @match_scheduler.error
    async def match_scheduler_error(error: Exception) -> None:
        log.exception("match_scheduler loop crashed")

    @tasks.loop(minutes=5)
    async def cleanup_scheduler() -> None:
        """Mark matches that have passed their start time as ended."""
        now = now_vn()

        try:
            with db_session_factory() as session:
                pending = (
                    session.query(Match)
                    .filter(Match.end_time.is_(None), Match.time_start < now)
                    .all()
                )
                for match in pending:
                    match.end_time = now
                session.commit()
        except Exception as exc:
            log.exception("cleanup_scheduler: DB error marking matches as ended")

    @cleanup_scheduler.error
    async def cleanup_scheduler_error(error: Exception) -> None:
        log.exception("cleanup_scheduler loop crashed")

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
            try:
                with db_session_factory() as session:
                    session.query(User).update(
                        {User.monthly_elo_gain: 0}, synchronize_session=False
                    )
                    session.commit()
                log.info("monthly_reset_scheduler: reset monthly_elo_gain for all users (%s-%02d)", *current_month)
            except Exception as exc:
                log.exception("monthly_reset_scheduler: DB error resetting monthly ELO")

    @monthly_reset_scheduler.error
    async def monthly_reset_scheduler_error(error: Exception) -> None:
        log.exception("monthly_reset_scheduler loop crashed")

    # Initialise the tracking attribute before the loop is ever started
    monthly_reset_scheduler._last_reset_month = (None, None)

    @tasks.loop(minutes=15)
    async def message_cleanup_scheduler() -> None:
        """Delete bot messages for matches that ended or were cancelled 6+ hours ago.

        Deletes:
        - The registration embed (match.register_message_id in REGISTER_CHANNEL_ID)
        - The check-in embed     (match.checkin_message_id  in CHECKIN_CHANNEL_ID)
        - Each lobby's result-entry embed (lobby.result_message_id in RESULT_CHANNEL_ID)

        Message IDs are set to NULL after deletion to avoid repeated attempts.
        """
        from entity import Lobby

        now = now_vn()
        cutoff = now - timedelta(hours=6)

        try:
            with db_session_factory() as session:
                matches_to_clean = (
                    session.query(Match)
                    .filter(
                        Match.end_time.isnot(None),
                        Match.end_time <= cutoff,
                    )
                    .all()
                )
                # Snapshot data to avoid detached-instance issues outside the session
                match_snapshots = [
                    (m.id, m.register_message_id, m.checkin_message_id)
                    for m in matches_to_clean
                    if m.register_message_id or m.checkin_message_id
                ]
                # Snapshot lobby data for result-message cleanup
                all_match_ids = [m.id for m in matches_to_clean]
                lobby_snapshots = []
                if all_match_ids:
                    lobbies = (
                        session.query(Lobby)
                        .filter(
                            Lobby.match_id.in_(all_match_ids),
                            Lobby.result_message_id.isnot(None),
                        )
                        .all()
                    )
                    lobby_snapshots = [(lb.id, lb.result_message_id) for lb in lobbies]
        except Exception as exc:
            log.exception("message_cleanup_scheduler: DB error fetching stale messages")
            return

        async def _try_delete(channel_id, message_id, label: str) -> None:
            if not channel_id or not message_id:
                return
            ch = bot.get_channel(channel_id)
            if ch is None:
                return
            try:
                msg = await ch.fetch_message(message_id)
                await msg.delete()
            except discord.NotFound:
                pass
            except Exception as exc:
                log.warning("Could not delete %s (msg=%s): %s", label, message_id, exc)

        # Delete match-level messages
        for match_id, reg_msg_id, checkin_msg_id in match_snapshots:
            await _try_delete(REGISTER_CHANNEL_ID, reg_msg_id, f"register_message match#{match_id}")
            await _try_delete(CHECKIN_CHANNEL_ID, checkin_msg_id, f"checkin_message match#{match_id}")
            try:
                with db_session_factory() as session:
                    db_match = session.get(Match, match_id)
                    if db_match:
                        db_match.register_message_id = None
                        db_match.checkin_message_id = None
                        session.commit()
            except Exception as exc:
                log.exception(
                    "message_cleanup_scheduler: DB error clearing message IDs for match #%s",
                    match_id,
                )

        # Delete lobby result-entry messages
        for lobby_id, result_msg_id in lobby_snapshots:
            await _try_delete(RESULT_CHANNEL_ID, result_msg_id, f"result_message lobby#{lobby_id}")
            try:
                with db_session_factory() as session:
                    db_lobby = session.get(Lobby, lobby_id)
                    if db_lobby:
                        db_lobby.result_message_id = None
                        session.commit()
            except Exception as exc:
                log.exception(
                    "message_cleanup_scheduler: DB error clearing result_message_id for lobby #%s",
                    lobby_id,
                )

    @message_cleanup_scheduler.error
    async def message_cleanup_scheduler_error(error: Exception) -> None:
        log.exception("message_cleanup_scheduler loop crashed")

    return match_scheduler, cleanup_scheduler, monthly_reset_scheduler, message_cleanup_scheduler
