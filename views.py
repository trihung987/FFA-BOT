"""
Discord UI components (Views and Modals) for the FFA bot.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import discord

from helpers import format_vn_time, parse_duration

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ── Shared helpers ─────────────────────────────────────────────────────────────


def _load_player_map(session, user_ids: list[int]) -> dict[int, str]:
    """Return a dict mapping Discord user ID → in-game name (fallback: 'Unknown')."""
    from entity import User

    if not user_ids:
        return {}
    users = session.query(User).filter(User.id.in_(user_ids)).all()
    return {u.id: (u.ingame_name or "Unknown") for u in users}


def build_registration_embed(match, p_map: dict[int, str]) -> discord.Embed:
    """Build (or rebuild) the registration embed for *match*.

    Parameters
    ----------
    match:
        A ``Match`` ORM instance (must still be attached to a session, or all
        needed attributes already loaded).
    p_map:
        Dict mapping Discord user ID (int) → in-game name (str).
    """
    time_start = match.time_start

    try:
        checkin_open_dt = time_start - parse_duration(match.time_reach_checkin)
        checkin_display = f"{format_vn_time(checkin_open_dt)} → {format_vn_time(time_start)}"
    except ValueError:
        checkin_display = "N/A"

    try:
        divide_open_dt = time_start - parse_duration(match.time_reach_divide_lobby)
        divide_display = f"{format_vn_time(divide_open_dt)} → {format_vn_time(time_start)}"
    except ValueError:
        divide_display = "N/A"

    registered = match.register_users_id or []
    map_names = match.name_maps or []

    reg_list_str = (
        "\n".join(f"<@{uid}> - {p_map.get(uid, 'Unknown')}" for uid in registered)
        or "_(chưa có ai)_"
    )

    embed = discord.Embed(
        title="🎮 Đăng Ký Tham Gia FFA Match",
        description=(
            f"🆔 **Match ID:** #{match.id}\n"
            f"🎯 **Số trận:** {match.count_fight}\n"
            f"📅 **Giờ bắt đầu:** {format_vn_time(time_start)}\n"
            f"⏰ **Check-in:** {checkin_display}\n"
            f"🔀 **Chia lobby:** {divide_display}\n"
            f"🗺️ **Maps:** {', '.join(map_names)}\n\n"
            f"👥 **Đã đăng ký: {len(registered)} người**\n"
            f"{reg_list_str}\n\n"
            "Nhấn **Tham gia** để đăng ký hoặc **Hủy đăng ký** để rút tên."
        ),
        color=discord.Color.blue(),
    )
    return embed


def build_checkin_embed(match, p_map: dict[int, str]) -> discord.Embed:
    """Build (or rebuild) the check-in embed for *match*.

    Parameters
    ----------
    match:
        A ``Match`` ORM instance.
    p_map:
        Dict mapping Discord user ID (int) → in-game name (str).
    """
    time_start = match.time_start
    registered = match.register_users_id or []
    checked_in = match.checkin_users_id or []

    checkin_list_str = (
        "\n".join(f"- {p_map.get(u, 'Unknown')} ✅" for u in checked_in)
        or "_(chưa có ai)_"
    )

    embed = discord.Embed(
        title="📋 Check-in FFA Match",
        description=(
            f"🆔 **Match ID:** #{match.id}\n"
            f"⏰ **Giờ kết thúc check-in:** {format_vn_time(time_start)}\n"
            f"👥 **Đã check-in:** {len(checked_in)}/{len(registered)}\n\n"
            f"✅ **Danh sách check-in:**\n{checkin_list_str}\n\n"
            "Nhấn **Sẵn sàng ✅** để xác nhận tham gia."
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"Match ID: {match.id}")
    return embed


# ── Modal: collect map names ───────────────────────────────────────────────────


class MapNamesModal(discord.ui.Modal):
    """
    Popup modal that collects one map name per fight.

    After submission the modal creates the match record and sends the
    registration embed (with Join / Cancel buttons) to the registration channel.
    """

    def __init__(
        self,
        count_fight: int,
        time_start: str,
        time_reach_checkin: str,
        time_reach_divide_lobby: str,
        db_session_factory,
        register_channel: discord.TextChannel,
    ) -> None:
        super().__init__(title=f"Nhập tên map cho {count_fight} trận")

        self.count_fight = count_fight
        self.time_start = time_start
        self.time_reach_checkin = time_reach_checkin
        self.time_reach_divide_lobby = time_reach_divide_lobby
        self.db_session_factory = db_session_factory
        self.register_channel = register_channel

        # Dynamically add one short-text input per fight
        self._map_inputs: list[discord.ui.TextInput] = []
        for i in range(1, count_fight + 1):
            field = discord.ui.TextInput(
                label=f"Tên map trận {i}",
                placeholder=f"Nhập tên map cho trận {i}",
                required=True,
                max_length=100,
            )
            self._map_inputs.append(field)
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from entity import Match

        map_names = [field.value for field in self._map_inputs]

        # Parse time_start string into a datetime object (Vietnam local time)
        try:
            time_start_dt = datetime.strptime(self.time_start, "%Y-%m-%d %H:%M")
        except ValueError:
            await interaction.response.send_message(
                "❌ Định dạng thời gian không hợp lệ. Vui lòng dùng: YYYY-MM-DD HH:MM",
                ephemeral=True,
            )
            return

        # Validate duration fields before creating the match
        try:
            parse_duration(self.time_reach_checkin)
        except ValueError:
            await interaction.response.send_message(
                f"❌ Định dạng thời gian check-in không hợp lệ: {self.time_reach_checkin!r}. "
                "Vui lòng dùng: 1h hoặc 30p",
                ephemeral=True,
            )
            return

        try:
            parse_duration(self.time_reach_divide_lobby)
        except ValueError:
            await interaction.response.send_message(
                f"❌ Định dạng thời gian chia lobby không hợp lệ: {self.time_reach_divide_lobby!r}. "
                "Vui lòng dùng: 1h hoặc 30p",
                ephemeral=True,
            )
            return

        # Save the match to the database and build the initial embed (no registrations yet)
        with self.db_session_factory() as session:
            match = Match(
                register_users_id=[],
                checkin_users_id=[],
                name_maps=map_names,
                count_fight=self.count_fight,
                time_start=time_start_dt,
                time_reach_checkin=self.time_reach_checkin,
                time_reach_divide_lobby=self.time_reach_divide_lobby,
            )
            session.add(match)
            session.commit()
            session.refresh(match)
            match_id = match.id
            embed = build_registration_embed(match, {})

        view = RegistrationView(match_id=match_id, db_session_factory=self.db_session_factory)
        reg_msg = await self.register_channel.send(embed=embed, view=view)

        # Persist the registration message ID so the scheduler can disable it later
        with self.db_session_factory() as session:
            db_match = session.get(Match, match_id)
            if db_match is not None:
                db_match.register_message_id = reg_msg.id
                session.commit()

        await interaction.response.send_message(
            f"✅ Đã mở đăng ký cho match #{match_id}!", ephemeral=True
        )


# ── View: registration embed with Join / Cancel buttons ───────────────────────


class RegistrationView(discord.ui.View):
    """
    Persistent view attached to the registration embed.
    Provides **Tham gia** (Join) and **Hủy đăng ký** (Cancel) buttons.
    The embed is rebuilt in-place on every action so the player list stays current.
    """

    def __init__(self, match_id: int, db_session_factory) -> None:
        # timeout=None makes the view persist until the bot restarts
        super().__init__(timeout=None)
        self.match_id = match_id
        self.db_session_factory = db_session_factory

    @discord.ui.button(label="Tham gia", style=discord.ButtonStyle.success, emoji="✅")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from entity import Match

        user_id = interaction.user.id

        with self.db_session_factory() as session:
            match: Match | None = session.get(Match, self.match_id)
            if match is None:
                await interaction.response.send_message("❌ Match không tồn tại.", ephemeral=True)
                return

            registered: list = (match.register_users_id or []).copy()
            if user_id in registered:
                await interaction.response.send_message(
                    "⚠️ Bạn đã đăng ký rồi!", ephemeral=True
                )
                return

            registered.append(user_id)
            match.register_users_id = registered
            session.commit()
            session.refresh(match)

            p_map = _load_player_map(session, registered)
            new_embed = build_registration_embed(match, p_map)

        # Edit the embed in-place; the updated list is the confirmation
        await interaction.response.edit_message(embed=new_embed, view=self)

    @discord.ui.button(label="Hủy đăng ký", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from entity import Match

        user_id = interaction.user.id

        with self.db_session_factory() as session:
            match: Match | None = session.get(Match, self.match_id)
            if match is None:
                await interaction.response.send_message("❌ Match không tồn tại.", ephemeral=True)
                return

            registered: list = (match.register_users_id or []).copy()
            if user_id not in registered:
                await interaction.response.send_message(
                    "⚠️ Bạn chưa đăng ký!", ephemeral=True
                )
                return

            registered.remove(user_id)
            match.register_users_id = registered
            session.commit()
            session.refresh(match)

            p_map = _load_player_map(session, registered)
            new_embed = build_registration_embed(match, p_map)

        await interaction.response.edit_message(embed=new_embed, view=self)


# ── View: check-in embed with Ready button ────────────────────────────────────


class CheckInView(discord.ui.View):
    """
    Persistent view attached to the check-in embed.
    Provides a **Sẵn sàng ✅** button for registered players to confirm attendance.
    The embed is rebuilt in-place on every check-in so the list stays current.
    """

    def __init__(self, match_id: int, db_session_factory) -> None:
        super().__init__(timeout=None)
        self.match_id = match_id
        self.db_session_factory = db_session_factory

    @discord.ui.button(label="Sẵn sàng ✅", style=discord.ButtonStyle.primary)
    async def ready(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from entity import Match

        user_id = interaction.user.id

        with self.db_session_factory() as session:
            match: Match | None = session.get(Match, self.match_id)
            if match is None:
                await interaction.response.send_message("❌ Match không tồn tại.", ephemeral=True)
                return

            registered: list = match.register_users_id or []
            if user_id not in registered:
                await interaction.response.send_message(
                    "⚠️ Bạn chưa đăng ký tham gia match này!", ephemeral=True
                )
                return

            checked_in: list = (match.checkin_users_id or []).copy()
            if user_id in checked_in:
                await interaction.response.send_message(
                    "⚠️ Bạn đã check-in rồi!", ephemeral=True
                )
                return

            checked_in.append(user_id)
            match.checkin_users_id = checked_in
            session.commit()
            session.refresh(match)

            # Load names for everyone relevant to the embed
            all_user_ids = list(set((match.register_users_id or []) + checked_in))
            p_map = _load_player_map(session, all_user_ids)
            new_embed = build_checkin_embed(match, p_map)

        await interaction.response.edit_message(embed=new_embed, view=self)

