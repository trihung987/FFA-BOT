"""
Entry point – assembles the bot from its modules and starts it.
"""

import discord
from discord import app_commands
from discord.ext import commands

from config import TOKEN, GUILD_ID
from database import SessionLocal
from commands import register_match_commands
from leaderboard import register_leaderboard_commands
from scheduler import setup_scheduler

guild_obj = discord.Object(id=GUILD_ID)

# ── Bot setup ──────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="/", intents=intents)

# Register all slash commands
register_match_commands(bot, SessionLocal)
register_leaderboard_commands(bot, SessionLocal)

# Create background schedulers (they are started inside on_ready)
match_scheduler, cleanup_scheduler = setup_scheduler(bot, SessionLocal)


# ── Global error handler ───────────────────────────────────────────────────────

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.CheckFailure):
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ Bạn không thể sử dụng lệnh này tại đây!", ephemeral=True
            )
        return
    if isinstance(error, app_commands.CommandInvokeError):
        original = error.original
        if isinstance(original, discord.NotFound) and original.code == 10062:
            # Interaction token expired before the bot could respond – silently ignore.
            return
    print(f"Command Error: {error}")


# ── Lifecycle events ───────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    if not match_scheduler.is_running():
        match_scheduler.start()
    if not cleanup_scheduler.is_running():
        cleanup_scheduler.start()
    await bot.tree.sync(guild=guild_obj)
    print(f"Logged in as {bot.user}")


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(TOKEN)