"""
Entry point – assembles the bot from its modules and starts it.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import TOKEN, GUILD_ID
from database import SessionLocal
from commands import register_match_commands
from leaderboard import register_leaderboard_commands
from scheduler import setup_scheduler

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

guild_obj = discord.Object(id=GUILD_ID)

# ── Bot setup ──────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="/", intents=intents)

# Register all slash commands
register_match_commands(bot, SessionLocal)
register_leaderboard_commands(bot, SessionLocal)

# Create background schedulers (they are started inside on_ready)
match_scheduler, cleanup_scheduler, monthly_reset_scheduler, message_cleanup_scheduler = setup_scheduler(bot, SessionLocal)


# ── Global error handler ───────────────────────────────────────────────────────

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    cmd_name = interaction.command.name if interaction.command else "unknown"
    user_id = interaction.user.id

    if isinstance(error, app_commands.errors.CheckFailure):
        if not interaction.response.is_done():
            try:
                await interaction.response.send_message(
                    "❌ Bạn không thể sử dụng lệnh này tại đây!", ephemeral=True
                )
            except discord.NotFound as exc:
                if exc.code == 10062:
                    log.warning(
                        "Interaction expired while sending CheckFailure response "
                        "(command=%r, user=%s)", cmd_name, user_id
                    )
                else:
                    log.error(
                        "NotFound error sending CheckFailure response "
                        "(command=%r, user=%s): %s", cmd_name, user_id, exc
                    )
            except discord.HTTPException as exc:
                log.error(
                    "HTTP error sending CheckFailure response "
                    "(command=%r, user=%s): %s", cmd_name, user_id, exc
                )
        return

    if isinstance(error, app_commands.CommandInvokeError):
        original = error.original
        if isinstance(original, discord.NotFound) and original.code == 10062:
            log.warning(
                "Interaction token expired (10062) – command=%r, user=%s",
                cmd_name, user_id,
            )
            return

    log.exception(
        "Unhandled app command error (command=%r, user=%s): %s",
        cmd_name, user_id, error,
    )


# ── Lifecycle events ───────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    if not match_scheduler.is_running():
        match_scheduler.start()
    if not cleanup_scheduler.is_running():
        cleanup_scheduler.start()
    if not monthly_reset_scheduler.is_running():
        monthly_reset_scheduler.start()
    if not message_cleanup_scheduler.is_running():
        message_cleanup_scheduler.start()
    try:
        await bot.tree.sync(guild=guild_obj)
    except Exception as exc:
        log.exception("Failed to sync command tree")
    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Starting bot...")
    bot.run(TOKEN)
    print("bot stopped")