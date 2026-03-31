"""
Leaderboard slash commands.
"""

import discord
from discord import app_commands
from discord.ext import commands as ext_commands

from config import GUILD_ID
from entity import User

guild_obj = discord.Object(id=GUILD_ID)


def register_leaderboard_commands(bot: ext_commands.Bot, db_session_factory) -> None:
    """Attach leaderboard-related slash commands to *bot*."""

    @bot.tree.command(
        name="leaderboard",
        description="Hiển thị bảng xếp hạng ELO.",
        guild=guild_obj,
    )
    async def leaderboard(interaction: discord.Interaction) -> None:
        with db_session_factory() as session:
            top_users = (
                session.query(User)
                .order_by(User.elo.desc())
                .limit(10)
                .all()
            )

        if not top_users:
            await interaction.response.send_message(
                "Chưa có người dùng nào trong hệ thống.", ephemeral=True
            )
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
        await interaction.response.send_message(embed=embed)
