"""
Discord UI components (Views and Modals) for the FFA bot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


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
        from datetime import datetime
        from entity import Match

        map_names = [field.value for field in self._map_inputs]

        # Parse time_start string into a datetime object
        try:
            time_start_dt = datetime.strptime(self.time_start, "%Y-%m-%d %H:%M")
        except ValueError:
            await interaction.response.send_message(
                "❌ Định dạng thời gian không hợp lệ. Vui lòng dùng: YYYY-MM-DD HH:MM",
                ephemeral=True,
            )
            return

        # Save the match to the database
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

        # Build the registration embed
        embed = discord.Embed(
            title="🎮 Đăng Ký Tham Gia FFA Match",
            description=(
                f"**Số trận:** {self.count_fight}\n"
                f"**Giờ bắt đầu:** {time_start_dt.strftime('%d/%m/%Y %H:%M')}\n"
                f"**Check-in trước:** {self.time_reach_checkin}\n"
                f"**Chia lobby trước:** {self.time_reach_divide_lobby}\n\n"
                f"**Maps:** {', '.join(map_names)}\n\n"
                "Nhấn **Tham gia** để đăng ký hoặc **Hủy đăng ký** để rút tên."
            ),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"Match ID: {match_id}")

        view = RegistrationView(match_id=match_id, db_session_factory=self.db_session_factory)
        await self.register_channel.send(embed=embed, view=view)

        await interaction.response.send_message(
            f"✅ Đã mở đăng ký cho match #{match_id}!", ephemeral=True
        )


# ── View: registration embed with Join / Cancel buttons ───────────────────────


class RegistrationView(discord.ui.View):
    """
    Persistent view attached to the registration embed.
    Provides **Tham gia** (Join) and **Hủy đăng ký** (Cancel) buttons.
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

        await interaction.response.send_message(
            f"✅ **{interaction.user.display_name}** đã đăng ký thành công!", ephemeral=True
        )

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

        await interaction.response.send_message(
            f"✅ **{interaction.user.display_name}** đã hủy đăng ký.", ephemeral=True
        )
