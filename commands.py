"""
Slash commands for trận management.
"""

import logging
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands as ext_commands
from typing import Optional

from config import GUILD_ID, REGISTER_CHANNEL_ID
from helpers import now_vn, safe_send_interaction, is_interaction_expired
from views import MapNamesModal

log = logging.getLogger(__name__)
guild_obj = discord.Object(id=GUILD_ID)


def register_match_commands(bot: ext_commands.Bot, db_session_factory) -> None:
    """Attach all trận-related slash commands to *bot*."""

    async def _change_ticket(
        interaction: discord.Interaction,
        player: discord.Member,
        amount: int,
        *,
        add: bool,
        context: str,
    ) -> None:
        from entity import User

        if amount <= 0:
            await safe_send_interaction(
                interaction,
                context,
                "❌ Số vé phải lớn hơn 0.",
                ephemeral=True,
            )
            return

        action = "thêm" if add else "xóa"
        try:
            with db_session_factory() as session:
                user = session.get(User, player.id)
                if user is None:
                    missing_profile_msg = (
                        f"❌ Người chơi {player.mention} chưa có hồ sơ. "
                        "Hãy yêu cầu họ dùng `/set_ingame_name` trước."
                        if add
                        else f"❌ Người chơi {player.mention} chưa có hồ sơ."
                    )
                    await safe_send_interaction(
                        interaction,
                        context,
                        missing_profile_msg,
                        ephemeral=True,
                    )
                    return

                if add:
                    user.ticket += amount
                else:
                    if user.ticket < amount:
                        await safe_send_interaction(
                            interaction,
                            context,
                            f"❌ {player.mention} chỉ có **{user.ticket}** vé, "
                            f"không đủ để xóa **{amount}** vé.",
                            ephemeral=True,
                        )
                        return
                    user.ticket -= amount

                new_total = user.ticket
                session.commit()
        except Exception:
            log.exception(
                "DB error in %s (admin=%s, player=%s)",
                context,
                interaction.user.id,
                player.id,
            )
            await safe_send_interaction(
                interaction,
                context,
                "❌ Đã xảy ra lỗi nội bộ khi lưu dữ liệu.",
                ephemeral=True,
            )
            return

        done_msg = (
            f"✅ Đã thêm **{amount}** vé cho {player.mention}. "
            f"Tổng vé hiện tại: **{new_total}**."
            if add
            else f"✅ Đã xóa **{amount}** vé của {player.mention}. "
            f"Tổng vé còn lại: **{new_total}**."
        )
        await safe_send_interaction(interaction, context, done_msg, ephemeral=True)

    @bot.tree.command(
        name="open_registration",
        description="Mở đăng ký cho một FFA trận mới.",
        guild=guild_obj,
    )
    @app_commands.describe(
        count_fight="Số trận đánh (n)",
        time_start="Thời gian bắt đầu (định dạng: YYYY-MM-DD HH:MM)",
        time_reach_checkin="Thời gian mở check-in trước khi bắt đầu (VD: 1h hoặc 30p)",
        time_reach_divide_lobby="Thời gian chia lobby trước khi bắt đầu (VD: 30p)",
    )
    async def open_registration(
        interaction: discord.Interaction,
        count_fight: int,
        time_start: str,
        time_reach_checkin: str,
        time_reach_divide_lobby: str,
    ) -> None:
        """Open registration for a new FFA trận."""

        if count_fight < 1 or count_fight > 5:
            await safe_send_interaction(
                interaction, "open_registration",
                "❌ Số trận đánh phải từ 1 đến 5.", ephemeral=True,
            )
            return

        register_channel = interaction.client.get_channel(REGISTER_CHANNEL_ID)
        if register_channel is None:
            log.error(
                "open_registration: register channel %s not found (user=%s)",
                REGISTER_CHANNEL_ID, interaction.user.id,
            )
            await safe_send_interaction(
                interaction, "open_registration",
                "❌ Không tìm thấy kênh đăng ký. Vui lòng kiểm tra cấu hình.", ephemeral=True,
            )
            return

        modal = MapNamesModal(
            count_fight=count_fight,
            time_start=time_start,
            time_reach_checkin=time_reach_checkin,
            time_reach_divide_lobby=time_reach_divide_lobby,
            db_session_factory=db_session_factory,
            register_channel=register_channel,
        )
        try:
            await interaction.response.send_modal(modal)
        except discord.NotFound as exc:
            if is_interaction_expired(exc):
                log.warning(
                    "Interaction expired sending open_registration modal (user=%s)",
                    interaction.user.id,
                )
            else:
                log.error(
                    "NotFound sending open_registration modal (user=%s): %s",
                    interaction.user.id, exc,
                )
        except discord.HTTPException as exc:
            log.error(
                "HTTP error sending open_registration modal (user=%s): %s",
                interaction.user.id, exc,
            )

    @open_registration.autocomplete("time_start")
    async def time_start_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Suggest the next 24 full-hour slots in Vietnam time (UTC+7)."""
        base = now_vn().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        choices: list[app_commands.Choice[str]] = []
        for i in range(24):
            value = (base + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M")
            if current in value:
                choices.append(app_commands.Choice(name=value, value=value))
            if len(choices) >= 25:
                break
        return choices

    _DURATION_PRESETS = ["15p", "20p", "30p", "45p", "1h", "1h30p", "2h"]

    def _duration_choices(current: str) -> list[app_commands.Choice[str]]:
        """Return up to 25 duration choices for *current* input.

        If the user typed a plain number (e.g. ``30``), suggest ``<n>p``
        (minutes) and ``<n>h`` (hours) first, then append any presets that
        contain the typed string.  Otherwise filter the preset list by the
        typed string.
        """
        choices: list[app_commands.Choice[str]] = []
        stripped = current.strip()
        if stripped.isdigit():
            generated = [f"{stripped}p", f"{stripped}h"]
            for val in generated:
                choices.append(app_commands.Choice(name=val, value=val))
            for p in _DURATION_PRESETS:
                if p not in generated and stripped in p:
                    choices.append(app_commands.Choice(name=p, value=p))
        else:
            needle = stripped.lower()
            for p in _DURATION_PRESETS:
                if needle in p:
                    choices.append(app_commands.Choice(name=p, value=p))
        return choices[:25]

    @open_registration.autocomplete("time_reach_checkin")
    async def time_reach_checkin_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Suggest common check-in open durations."""
        return _duration_choices(current)

    @open_registration.autocomplete("time_reach_divide_lobby")
    async def time_reach_divide_lobby_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Suggest common lobby-divide durations."""
        return _duration_choices(current)

    @bot.tree.command(
        name="set_ingame_name",
        description="[Admin] Đặt tên in-game và ELO cho @người chơi. Tạo mới nếu chưa có hồ sơ.",
        guild=guild_obj,
    )
    @app_commands.describe(
        player="Người chơi cần đặt thông tin",
        name="Tên in-game trong game",
        elo="ELO của người chơi (mặc định 1000 khi tạo mới)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def set_ingame_name(
        interaction: discord.Interaction,
        player: discord.Member,
        name: str,
        elo: Optional[int] = None,
    ) -> None:
        """[Admin] Set or update in-game name and ELO for a target player.

        Creates a new profile if the player has none, otherwise updates the
        existing record.  Only server administrators may use this command.
        """
        from entity import User

        try:
            with db_session_factory() as session:
                user = session.get(User, player.id)
                if user is None:
                    initial_elo = elo if elo is not None else 1000
                    user = User(id=player.id, ingame_name=name, elo=initial_elo)
                    session.add(user)
                    msg = (
                        f"✅ Đã tạo hồ sơ cho {player.mention}: "
                        f"tên in-game **{name}**, ELO **{initial_elo}**."
                    )
                else:
                    user.ingame_name = name
                    if elo is not None:
                        delta = elo - user.elo
                        user.elo = elo
                        user.last_elo_change = delta
                        user.updated_date = now_vn()
                        user.monthly_elo_gain = (user.monthly_elo_gain or 0) + delta
                    msg = f"✅ Đã cập nhật hồ sơ cho {player.mention}: tên in-game **{name}**"
                    if elo is not None:
                        msg += f", ELO: **{elo}**"
                    msg += "."
                session.commit()
        except Exception as exc:
            log.exception("DB error in set_ingame_name (admin=%s, player=%s)", interaction.user.id, player.id)
            await safe_send_interaction(
                interaction, "set_ingame_name",
                "❌ Đã xảy ra lỗi nội bộ khi lưu dữ liệu.", ephemeral=True,
            )
            return

        await safe_send_interaction(interaction, "set_ingame_name", msg, ephemeral=True)

    # ── Admin: add ticket ──────────────────────────────────────────────────────

    @bot.tree.command(
        name="add_ticket",
        description="[Admin] Thêm vé cho người chơi.",
        guild=guild_obj,
    )
    @app_commands.describe(player="Người chơi cần thêm vé", amount="Số vé cần thêm (mặc định 1)")
    @app_commands.checks.has_permissions(administrator=True)
    async def add_ticket(
        interaction: discord.Interaction,
        player: discord.Member,
        amount: int = 1,
    ) -> None:
        """Admin: add *amount* tickets to a player's account."""
        await _change_ticket(interaction, player, amount, add=True, context="add_ticket")

    # ── Admin: remove ticket ───────────────────────────────────────────────────

    @bot.tree.command(
        name="remove_ticket",
        description="[Admin] Xóa vé của người chơi.",
        guild=guild_obj,
    )
    @app_commands.describe(player="Người chơi cần xóa vé", amount="Số vé cần xóa (mặc định 1)")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_ticket(
        interaction: discord.Interaction,
        player: discord.Member,
        amount: int = 1,
    ) -> None:
        """Admin: remove *amount* tickets from a player's account."""
        await _change_ticket(interaction, player, amount, add=False, context="remove_ticket")

    # ── View player FFA stats ─────────────────────────────────────────────────

    async def _send_ffa_profile(
        interaction: discord.Interaction,
        player: discord.abc.User,
        *,
        context: str,
    ) -> None:
        """Build and send a player's FFA profile embed."""
        from entity import Lobby, User
        from helpers import get_rank

        def _to_int(value: object) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        try:
            with db_session_factory() as session:
                user = session.get(User, player.id)
                lobbies: list[Lobby] = (
                    session.query(Lobby)
                    .filter(Lobby.status == "finished")
                    .filter(Lobby.users_list.contains([player.id]))
                    .all()
                )
        except Exception:
            log.exception(
                "DB error in %s (requester=%s, player=%s)",
                context,
                interaction.user.id,
                player.id,
            )
            await safe_send_interaction(
                interaction,
                context,
                "❌ Đã xảy ra lỗi nội bộ khi truy vấn dữ liệu.",
                ephemeral=True,
            )
            return

        if user is None:
            await safe_send_interaction(
                interaction,
                context,
                f"❌ {player.mention} chưa có hồ sơ FFA. "
                "Vui lòng tạo profile cho họ bằng lệnh /set_ingame_name.",
                ephemeral=True,
            )
            return

        played_match_ids: set[int] = set()
        best_single_fight_score = 0
        for lobby in lobbies:
            played_match_ids.add(int(lobby.match_id))

            score_map = lobby.scores or {}
            for fight_scores in score_map.values():
                if not isinstance(fight_scores, dict):
                    continue
                fight_score = _to_int(fight_scores.get(str(player.id), 0))
                if fight_score > best_single_fight_score:
                    best_single_fight_score = fight_score

        total_matches_played = len(played_match_ids)

        # Format last ELO change indicator
        change = user.last_elo_change or 0
        if change > 0:
            change_str = f"📈 +{change}"
        elif change < 0:
            change_str = f"📉 {change}"
        else:
            change_str = "—"

        # Format monthly ELO gain — only valid if last updated in current month
        now = now_vn()
        current_month = (now.year, now.month)
        update_month = (user.updated_date.year, user.updated_date.month) if user.updated_date else None
        
        if update_month == current_month:
            monthly = user.monthly_elo_gain or 0
            if monthly > 0:
                monthly_str = f"🔥 +{monthly}"
            elif monthly < 0:
                monthly_str = f"📉 {monthly}"
            else:
                monthly_str = "—"
        else:
            monthly_str = "—"

        rank_str = get_rank(user.elo)

        embed = discord.Embed(
            title=f"🎮 Hồ Sơ FFA — {player.display_name}",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=player.display_avatar.url)
        embed.add_field(name="🕹️ Tên in-game", value=user.ingame_name or "_chưa đặt_", inline=True)
        embed.add_field(name="⚔️ ELO", value=f"**{user.elo}**", inline=True)
        embed.add_field(name="🏅 Hạng", value=rank_str, inline=True)
        embed.add_field(name="🎫 Vé", value=str(user.ticket), inline=True)
        embed.add_field(name="📈 Biến động gần nhất", value=change_str, inline=True)
        embed.add_field(name="🔥 ELO tăng tháng này", value=monthly_str, inline=True)
        embed.add_field(name="🎮 Tổng số trận đã tham gia", value=str(total_matches_played), inline=True)
        embed.add_field(name="🏆 Điểm số cao nhất 1 trận", value=str(best_single_fight_score), inline=True)
        embed.add_field(
            name="📅 Tham gia từ",
            value=user.created_date.strftime("%d/%m/%Y") if user.created_date else "—",
            inline=True,
        )
        embed.set_footer(text=f"Discord ID: {player.id}")

        await safe_send_interaction(interaction, context, embed=embed)

    @bot.tree.command(
        name="view_ffa",
        description="Xem thông tin FFA của một người chơi.",
        guild=guild_obj,
    )
    @app_commands.describe(player="Người chơi cần xem thông tin")
    async def view_ffa(
        interaction: discord.Interaction,
        player: discord.Member,
    ) -> None:
        """Display a rich embed with a player's FFA stats."""
        await _send_ffa_profile(interaction, player, context="view_ffa")

    @bot.tree.command(
        name="ffa_me",
        description="Xem thông tin FFA của chính bạn.",
        guild=guild_obj,
    )
    async def ffa_me(interaction: discord.Interaction) -> None:
        """Display a rich embed with the invoker's own FFA stats."""
        await _send_ffa_profile(interaction, interaction.user, context="ffa_me")

    # ── Admin: test full flow ─────────────────────────────────────────────────

    @bot.tree.command(
        name="test_flow",
        description="[Admin] Test nhanh toàn bộ quy trình FFA: tạo trận → checkin → chia lobby → nhập kết quả.",
        guild=guild_obj,
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def test_flow(interaction: discord.Interaction) -> None:
        """[Admin] Instantly simulate the full FFA match flow for quick testing.

        Creates a match with the invoking admin as the sole real player plus 7 AI
        slots (total = 8), skipping all time-based scheduling steps, and posts
        embeds for every stage so the admin can inspect each phase and submit
        test results immediately.
        """
        from entity import Match, User, Lobby
        from views import (
            RegistrationView,
            LobbyResultView,
            build_registration_embed,
            build_checkin_embed,
            build_lobby_result_message_assets,
            build_disabled_registration_view,
            build_disabled_checkin_view,
            build_registered_mentions,
        )
        from lobby_division import (
            assign_civs,
            TIER_RECRUIT,
            create_lobby_channels,
            build_lobby_display_embed,
            build_lobby_display_messages,
            build_lobby_display_image_file,
            _build_emoji_map,
            _resolve_emoji_str,
        )
        from config import (
            REGISTER_CHANNEL_ID as _REG_CH,
            CHECKIN_CHANNEL_ID as _CHECKIN_CH,
            DIVIDE_LOBBY_CHANNEL_ID as _DIVIDE_CH,
            RESULT_CHANNEL_ID as _RESULT_CH,
            JUDGE_ROLE_ID as _JUDGE_ROLE_ID,
            LOBBY_CATEGORY_ID as _LOBBY_CATEGORY_ID,
        )

        admin_id = interaction.user.id

        async def _send_testflow_msg(message: str) -> None:
            """Send ephemeral status/error text regardless of interaction state."""
            await safe_send_interaction(interaction, "test_flow", message, ephemeral=True)

        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True)
            except discord.HTTPException as exc:
                # 40060: response was already acknowledged elsewhere.
                if exc.code != 40060:
                    log.error("test_flow: failed to defer interaction (user=%s): %s", admin_id, exc)
                    await _send_testflow_msg("❌ Không thể bắt đầu test flow do lỗi phản hồi interaction.")
                    return

        # ── 1. Ensure admin has a user profile ─────────────────────────────────
        ingame_name = interaction.user.display_name
        try:
            with db_session_factory() as session:
                admin_user = session.get(User, admin_id)
                if admin_user is None:
                    admin_user = User(
                        id=admin_id,
                        ingame_name=ingame_name[:64],
                        elo=1000,
                        ticket=0,
                    )
                    session.add(admin_user)
                    session.commit()
                else:
                    ingame_name = admin_user.ingame_name or ingame_name
        except Exception:
            log.exception("test_flow: DB error ensuring user profile (user=%s)", admin_id)
            await _send_testflow_msg("❌ Lỗi DB khi kiểm tra hồ sơ người dùng.")
            return

        # ── 2. Create match ────────────────────────────────────────────────────
        try:
            with db_session_factory() as session:
                match = Match(
                    register_users_id=[admin_id],
                    checkin_users_id=[admin_id],
                    name_maps=["Map 1", "Map 2", "Map 3"],
                    count_fight=3,
                    time_start=now_vn() + timedelta(hours=2),
                    time_reach_checkin="2h",
                    time_reach_divide_lobby="1h",
                    status="checkin",
                )
                session.add(match)
                session.commit()
                session.refresh(match)
                match_id = match.id
        except Exception:
            log.exception("test_flow: DB error creating match (user=%s)", admin_id)
            await _send_testflow_msg("❌ Lỗi DB khi tạo trận.")
            return

        p_map = {admin_id: ingame_name}

        # ── 3. Post registration embed (closed – checkin already started) ──────
        reg_channel = interaction.client.get_channel(_REG_CH) if _REG_CH else None
        if reg_channel:
            try:
                with db_session_factory() as session:
                    db_match = session.get(Match, match_id)
                    reg_embed = build_registration_embed(db_match, p_map, checkin_started=True)
                reg_msg = await reg_channel.send(embed=reg_embed, view=build_disabled_registration_view())
                with db_session_factory() as session:
                    db_match = session.get(Match, match_id)
                    if db_match:
                        db_match.register_message_id = reg_msg.id
                        session.commit()
            except Exception:
                log.exception("test_flow: failed to send registration embed (match=%s)", match_id)

        # ── 4. Post checkin embed (closed – lobby division about to start) ─────
        checkin_channel = interaction.client.get_channel(_CHECKIN_CH) if _CHECKIN_CH else None
        if checkin_channel:
            try:
                with db_session_factory() as session:
                    db_match = session.get(Match, match_id)
                    checkin_embed = build_checkin_embed(db_match, p_map, ended=True)
                    registered_mentions = build_registered_mentions(db_match.register_users_id if db_match else [])
                checkin_msg = await checkin_channel.send(
                    content=registered_mentions,
                    embed=checkin_embed,
                    view=build_disabled_checkin_view(),
                )
                with db_session_factory() as session:
                    db_match = session.get(Match, match_id)
                    if db_match:
                        db_match.checkin_message_id = checkin_msg.id
                        session.commit()
            except Exception:
                log.exception("test_flow: failed to send checkin embed (match=%s)", match_id)

        # ── 5. Create lobby with 1 real player + 7 AI ─────────────────────────
        ai_count = 7
        all_civ_keys = [str(admin_id)] + [f"AI_{i}" for i in range(1, ai_count + 1)]
        try:
            civs = assign_civs(all_civ_keys, 6)
        except (ValueError, Exception):
            log.exception("test_flow: civ assignment failed (match=%s)", match_id)
            civs = {}

        # Resolve :name: emoji strings → <:name:id> using guild's custom emojis
        if interaction.guild:
            emoji_map = _build_emoji_map(interaction.guild)
            if emoji_map:
                civs = {
                    key: [_resolve_emoji_str(c, emoji_map) for c in civ_list]
                    for key, civ_list in civs.items()
                }

        try:
            with db_session_factory() as session:
                lobby = Lobby(
                    match_id=match_id,
                    tier=TIER_RECRUIT,
                    lobby_number=1,
                    users_list=[admin_id],
                    ai_count=ai_count,
                    civs=civs,
                    scores={},
                    status="active",
                    voice_channel_ids=[],
                    text_channel_ids=[],
                )
                session.add(lobby)
                session.commit()
                session.refresh(lobby)
                lobby_id = lobby.id
        except Exception:
            log.exception("test_flow: DB error creating lobby (match=%s)", match_id)
            await _send_testflow_msg("❌ Lỗi DB khi tạo lobby.")
            return

        # Create voice + text channels like the normal lobby-division flow.
        if interaction.guild:
            try:
                guild = interaction.guild
                judge_role = guild.get_role(_JUDGE_ROLE_ID) if _JUDGE_ROLE_ID else None
                category_obj = guild.get_channel(_LOBBY_CATEGORY_ID) if _LOBBY_CATEGORY_ID else None
                category = category_obj if isinstance(category_obj, discord.CategoryChannel) else None

                if _LOBBY_CATEGORY_ID and category is None:
                    log.warning(
                        "test_flow: configured LOBBY_CATEGORY_ID=%s is missing or not a category channel",
                        _LOBBY_CATEGORY_ID,
                    )

                with db_session_factory() as session:
                    db_lobby = session.get(Lobby, lobby_id)
                    db_match = session.get(Match, match_id)
                    if db_lobby and db_match:
                        voice_ids, text_ids = await create_lobby_channels(
                            guild,
                            db_lobby,
                            db_match,
                            p_map,
                            category,
                            judge_role,
                        )
                        db_lobby.voice_channel_ids = voice_ids
                        db_lobby.text_channel_ids = text_ids
                        session.commit()
            except Exception:
                log.exception("test_flow: channel creation failed (lobby=%s)", lobby_id)

        # Update match status to dividing
        try:
            with db_session_factory() as session:
                db_match = session.get(Match, match_id)
                if db_match:
                    db_match.status = "dividing"
                    session.commit()
        except Exception:
            log.exception("test_flow: DB error updating match status (match=%s)", match_id)

        # ── 6. Post lobby display embed ────────────────────────────────────────
        divide_channel = interaction.client.get_channel(_DIVIDE_CH) if _DIVIDE_CH else None
        if divide_channel:
            try:
                display_messages: list[str] = []
                display_file = None
                mentions = ""
                with db_session_factory() as session:
                    db_lobby = session.get(Lobby, lobby_id)
                    db_match = session.get(Match, match_id)
                    if db_lobby and db_match:
                        mentions = " ".join(f"<@{uid}>" for uid in (db_lobby.users_list or []))
                        display_file = await build_lobby_display_image_file(db_lobby, db_match, p_map)
                        if display_file is None:
                            display_messages = build_lobby_display_messages(db_lobby, db_match, p_map)
                if display_file is not None:
                    if db_lobby:
                        display_embed = build_lobby_display_embed(db_lobby, db_match)
                    else:
                        display_embed = discord.Embed(
                            title=f"🎮 Chia Lobby - Trận #{match_id}",
                            description="Không thể tải thông tin lobby.",
                            color=discord.Color.blue(),
                        )
                    await divide_channel.send(content=mentions or None, embed=display_embed, file=display_file)
                else:
                    if display_messages:
                        fallback_embed = discord.Embed(
                            title=f"🎮 Chia Lobby - Trận #{match_id}",
                            description=display_messages[0],
                            color=discord.Color.blue(),
                        )
                        await divide_channel.send(content=mentions or None, embed=fallback_embed)
                        for extra_message in display_messages[1:]:
                            await divide_channel.send(content=extra_message)
            except Exception:
                log.exception("test_flow: failed to send lobby display message (lobby=%s)", lobby_id)

        # ── 7. Post result entry embed with interactive buttons ────────────────
        result_channel = interaction.client.get_channel(_RESULT_CH) if _RESULT_CH else None
        if result_channel:
            try:
                result_embed = None
                result_file = None
                db_match = None
                p_map = None
                with db_session_factory() as session:
                    db_lobby = session.get(Lobby, lobby_id)
                    db_match = session.get(Match, match_id)
                    if db_lobby and db_match:
                        users_list = db_lobby.users_list or []
                        users = session.query(User).filter(User.id.in_(users_list)).all() if users_list else []
                        p_map = {u.id: (u.ingame_name or "Unknown") for u in users}
                        result_embed, result_file = build_lobby_result_message_assets(db_lobby, db_match, p_map)
                if result_embed is None:
                    raise RuntimeError(f"Cannot build result embed for lobby #{lobby_id}")
                result_view = LobbyResultView(
                    lobby_id=lobby_id,
                    count_fight=(db_match.count_fight if db_match else 6),
                    map_names=(db_match.name_maps if db_match else []),
                    db_session_factory=db_session_factory,
                )
                if result_file is not None:
                    result_msg = await result_channel.send(embed=result_embed, view=result_view, file=result_file)
                else:
                    result_msg = await result_channel.send(embed=result_embed, view=result_view)
                with db_session_factory() as session:
                    db_lobby = session.get(Lobby, lobby_id)
                    if db_lobby:
                        db_lobby.result_message_id = result_msg.id
                        session.commit()
            except Exception:
                log.exception("test_flow: failed to send result embed (lobby=%s)", lobby_id)

        await _send_testflow_msg(
            f"✅ **Test Flow hoàn tất!**\n"
            f"• ID trận: **#{match_id}**\n"
            f"• Người chơi thật: {interaction.user.mention} (in-game: **{ingame_name}**)\n"
            f"• AI slots: **7**\n"
            f"• Số trận: **6** (Map 1 → Map 6)\n"
            f"• Lobby **{TIER_RECRUIT} #1** đã được tạo.\n"
            f"• Vào kênh kết quả để nhập điểm và test nút **Chốt Kết Quả ✅**.",
        )

    # ── Emoji test ────────────────────────────────────────────────────────────

    # @bot.tree.command(
    #     name="emojitest",
    #     description="Kiểm tra chuỗi emoji custom: nhập chuỗi bất kỳ, bot sẽ gửi lại trong embed để xem kết quả.",
    #     guild=guild_obj,
    # )
    # @app_commands.describe(text="Chuỗi cần kiểm tra (VD: <:ten_emoji:123456789>)")
    # async def emojitest(interaction: discord.Interaction, text: str) -> None:
    #     """Echo *text* back inside an embed so the user can verify custom emoji strings."""
    #     embed = discord.Embed(
    #         title="🔍 Kiểm Tra Emoji",
    #         description=text,
    #         color=discord.Color.og_blurple(),
    #     )
    #     embed.add_field(name="Chuỗi gốc (raw)", value=f"`{discord.utils.escape_markdown(text)}`", inline=False)
    #     embed.set_footer(text=f"Yêu cầu bởi {interaction.user.display_name}")
    #     await safe_send_interaction(interaction, "emojitest", embed=embed)

    # # ── Generic embed echo ───────────────────────────────────────────────────

    # @bot.tree.command(
    #     name="embed_echo",
    #     description="In ra một embed với nội dung chuỗi bạn nhập.",
    #     guild=guild_obj,
    # )
    # @app_commands.describe(text="Nội dung muốn hiển thị trong embed")
    # async def embed_echo(interaction: discord.Interaction, text: str) -> None:
    #     """Render user-provided text in a simple embed for quick testing."""
    #     content = text.strip()
    #     if not content:
    #         await safe_send_interaction(interaction, "embed_echo", "❌ Vui lòng nhập nội dung không rỗng.", ephemeral=True)
    #         return

    #     if len(content) > 4000:
    #         await safe_send_interaction(
    #             interaction,
    #             "embed_echo",
    #             "❌ Nội dung quá dài cho phần mô tả embed (tối đa 4000 ký tự).",
    #             ephemeral=True,
    #         )
    #         return

    #     embed = discord.Embed(
    #         title="🧪 Embed Echo",
    #         description="<:tughlaq_dynasty:1490419988157431929> <:tughlaq_dynasty:1490419988157431929> <:tughlaq_dynasty:1490419988157431929> <:tughlaq_dynasty:1490419988157431929> <:tughlaq_dynasty:1490419988157431929> <:tughlaq_dynasty:1490419988157431929> <:tughlaq_dynasty:1490419988157431929> <:tughlaq_dynasty:1490419988157431929> <:tughlaq_dynasty:1490419988157431929> xin chào tổ quốc tôi yêu mến",
    #         color=discord.Color.blurple(),
    #     )
    #     embed.set_footer(text=f"Yêu cầu bởi {interaction.user.display_name}")
    #     await safe_send_interaction(interaction, "embed_echo", embed=embed)

    # # ── Emoji find ────────────────────────────────────────────────────────────

    # @bot.tree.command(
    #     name="emojifind",
    #     description="Tìm emoji custom theo tên trong server và hiển thị mã của nó.",
    #     guild=guild_obj,
    # )
    # @app_commands.describe(name="Tên emoji cần tìm (không cần dấu ngoặc hay dấu hai chấm)")
    # async def emojifind(interaction: discord.Interaction, name: str) -> None:
    #     """Search the guild's custom emojis by name and return their codes."""
    #     if interaction.guild is None:
    #         await safe_send_interaction(interaction, "emojifind", "❌ Lệnh này chỉ dùng được trong server.", ephemeral=True)
    #         return

    #     query = name.lower().strip()
    #     matches = [e for e in interaction.guild.emojis if query in e.name.lower()]

    #     if not matches:
    #         await safe_send_interaction(
    #             interaction, "emojifind",
    #             f"❌ Không tìm thấy emoji nào chứa tên **{discord.utils.escape_markdown(name)}** trong server.",
    #             ephemeral=True,
    #         )
    #         return

    #     lines = [f"{e}  →  `{e}`" for e in matches[:25]]
    #     embed = discord.Embed(
    #         title=f"🔎 Kết Quả Tìm Emoji — \"{name}\"",
    #         description="\n".join(lines),
    #         color=discord.Color.green(),
    #     )
    #     embed.set_footer(text=f"Tìm thấy {len(matches)} kết quả{' (hiển thị 25 đầu tiên)' if len(matches) > 25 else ''}")
    #     await safe_send_interaction(interaction, "emojifind", embed=embed)

    # # ── Emoji list ────────────────────────────────────────────────────────────

    # @bot.tree.command(
    #     name="emojilist",
    #     description="Liệt kê toàn bộ emoji custom của server kèm mã sử dụng.",
    #     guild=guild_obj,
    # )
    # async def emojilist(interaction: discord.Interaction) -> None:
    #     """List all custom emojis in the guild with their raw codes."""
    #     if interaction.guild is None:
    #         await safe_send_interaction(interaction, "emojilist", "❌ Lệnh này chỉ dùng được trong server.", ephemeral=True)
    #         return

    #     emojis = interaction.guild.emojis
    #     if not emojis:
    #         await safe_send_interaction(interaction, "emojilist", "ℹ️ Server chưa có emoji custom nào.", ephemeral=True)
    #         return

    #     # Split into pages of 20 to stay within embed limits
    #     page_size = 20
    #     pages = [emojis[i:i + page_size] for i in range(0, len(emojis), page_size)]
    #     embeds = []
    #     for idx, page in enumerate(pages, start=1):
    #         lines = [f"{e}  `{e}`" for e in page]
    #         embed = discord.Embed(
    #             title=f"😀 Danh Sách Emoji Server ({len(emojis)} emoji)",
    #             description="\n".join(lines),
    #             color=discord.Color.blurple(),
    #         )
    #         if len(pages) > 1:
    #             embed.set_footer(text=f"Trang {idx}/{len(pages)}")
    #         embeds.append(embed)

    #     # Send first embed as the response, remaining as follow-ups
    #     try:
    #         await interaction.response.send_message(embed=embeds[0])
    #         for extra_embed in embeds[1:]:
    #             await interaction.followup.send(embed=extra_embed)
    #     except discord.NotFound as exc:
    #         if exc.code == 10062:
    #             log.warning("Interaction expired for emojilist (user=%s)", interaction.user.id)
    #         else:
    #             log.error("NotFound in emojilist (user=%s): %s", interaction.user.id, exc)
    #     except discord.HTTPException as exc:
    #         log.error("HTTP error in emojilist (user=%s): %s", interaction.user.id, exc)
