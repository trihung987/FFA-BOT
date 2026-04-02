"""
Leaderboard slash commands.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands as ext_commands

from config import GUILD_ID
from entity import User

log = logging.getLogger(__name__)
guild_obj = discord.Object(id=GUILD_ID)


def register_leaderboard_commands(bot: ext_commands.Bot, db_session_factory) -> None:
    """Attach leaderboard-related slash commands to *bot*."""

    @bot.tree.command(
        name="leaderboard",
        description="Hiển thị bảng xếp hạng ELO.",
        guild=guild_obj,
    )
    async def leaderboard(interaction: discord.Interaction) -> None:
        try:
            with db_session_factory() as session:
                top_users = (
                    session.query(User)
                    .order_by(User.elo.desc())
                    .limit(10)
                    .all()
                )
        except Exception as exc:
            log.exception("DB error in leaderboard (user=%s)", interaction.user.id)
            if not interaction.response.is_done():
                try:
                    await interaction.response.send_message(
                        "❌ Đã xảy ra lỗi nội bộ khi truy vấn dữ liệu.", ephemeral=True
                    )
                except discord.HTTPException:
                    pass
            return

        if not top_users:
            try:
                await interaction.response.send_message(
                    "Chưa có người dùng nào trong hệ thống.", ephemeral=True
                )
            except discord.NotFound as exc:
                if exc.code == 10062:
                    log.warning("Interaction expired for leaderboard (user=%s)", interaction.user.id)
                else:
                    log.error("NotFound in leaderboard response (user=%s): %s", interaction.user.id, exc)
            return

        lines = []
        for rank, user in enumerate(top_users, start=1):
            member = interaction.guild.get_member(user.id)
            name = member.display_name if member else f"<@{user.id}>"
            lines.append(f"**#{rank}** {name} — ELO: **{user.elo}** | Vé: {user.ticket}")

        embed = discord.Embed(
            title="🏆 Bảng Xếp Hạng ELO",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        try:
            await interaction.response.send_message(embed=embed)
        except discord.NotFound as exc:
            if exc.code == 10062:
                log.warning("Interaction expired for leaderboard (user=%s)", interaction.user.id)
            else:
                log.error("NotFound sending leaderboard embed (user=%s): %s", interaction.user.id, exc)
        except discord.HTTPException as exc:
            log.error("HTTP error sending leaderboard embed (user=%s): %s", interaction.user.id, exc)
