"""
Slash commands for match management.
"""

import discord
from discord import app_commands
from discord.ext import commands as ext_commands

from config import GUILD_ID, REGISTER_CHANNEL_ID
from views import MapNamesModal

guild_obj = discord.Object(id=GUILD_ID)


def register_match_commands(bot: ext_commands.Bot, db_session_factory) -> None:
    """Attach all match-related slash commands to *bot*."""

    @bot.tree.command(
        name="open_registration",
        description="Mở đăng ký cho một FFA match mới.",
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
        """Open registration for a new FFA match."""

        if count_fight < 1 or count_fight > 5:
            await interaction.response.send_message(
                "❌ Số trận đánh phải từ 1 đến 5.", ephemeral=True
            )
            return

        register_channel = interaction.client.get_channel(REGISTER_CHANNEL_ID)
        if register_channel is None:
            await interaction.response.send_message(
                "❌ Không tìm thấy kênh đăng ký. Vui lòng kiểm tra cấu hình.", ephemeral=True
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
        await interaction.response.send_modal(modal)
