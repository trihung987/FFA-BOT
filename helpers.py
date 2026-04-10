import re
import logging
from datetime import datetime, timedelta, timezone

import discord

# Vietnam Standard Time - UTC+7 (no DST)
VN_TZ = timezone(timedelta(hours=7))
log = logging.getLogger(__name__)


def parse_duration(value: str) -> timedelta:
    """Convert a human-readable duration string to a :class:`timedelta`.

    Supported formats:
    - ``"1h"``    → 1 hour
    - ``"30p"``   → 30 minutes
    - ``"1h30p"`` → 1 hour 30 minutes
    """
    s = value.strip().lower()
    # Combined format: e.g. "1h30p"
    m = re.fullmatch(r"(\d+)h(\d+)p", s)
    if m:
        return timedelta(hours=int(m.group(1)), minutes=int(m.group(2)))
    # Single-component format: e.g. "1h" or "30p"
    m = re.fullmatch(r"(\d+)([hp])", s)
    if not m:
        raise ValueError(f"Unrecognised duration format: {value!r}")
    amount = int(m.group(1))
    unit = m.group(2)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(minutes=amount)


def now_vn() -> datetime:
    """Return the current naive datetime in Vietnam timezone (UTC+7).

    Naive (tzinfo=None) is intentional: match_time values stored in the
    database are also naive Vietnam-local datetimes, so arithmetic such as
    ``match_time - now_vn()`` works without mixing aware/naive types.
    """
    return datetime.now(tz=VN_TZ).replace(tzinfo=None)


def format_vnd(amount: int) -> str:
    return f"{amount:,.0f} VNĐ".replace(",", ".")


def format_vn_time(dt: datetime) -> str:
    """Format: 14:30 - Ngày 25/12/2026"""
    return dt.strftime("%H:%M - Ngày %d/%m/%Y")


def is_interaction_expired(exc: discord.NotFound) -> bool:
    return exc.code == 10062


async def safe_send_interaction(interaction: discord.Interaction, context: str, *args, **kwargs) -> None:
    """Send an interaction response or follow-up, logging timeout/HTTP errors."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(*args, **kwargs)
        else:
            await interaction.response.send_message(*args, **kwargs)
    except discord.InteractionResponded:
        try:
            await interaction.followup.send(*args, **kwargs)
        except discord.NotFound as exc:
            if is_interaction_expired(exc):
                log.warning("Interaction expired (%s, user=%s)", context, interaction.user.id)
            else:
                log.error("NotFound sending follow-up (%s, user=%s): %s", context, interaction.user.id, exc)
        except discord.HTTPException as exc:
            log.error("HTTP error sending follow-up (%s, user=%s): %s", context, interaction.user.id, exc)
    except discord.NotFound as exc:
        if is_interaction_expired(exc):
            log.warning("Interaction expired (%s, user=%s)", context, interaction.user.id)
        else:
            log.error("NotFound sending response (%s, user=%s): %s", context, interaction.user.id, exc)
    except discord.HTTPException as exc:
        log.error("HTTP error sending response (%s, user=%s): %s", context, interaction.user.id, exc)


# ELO tier thresholds (inclusive lower bound, exclusive upper bound)
_RANKS = [
    (2100, "🏆 Challenger"),
    (1800, "🌟 Legendary"),
    (1500, "💎 Diamond"),
    (1200, "🔷 Platinum"),
    (1000, "🥇 Gold"),
    (700,  "🥈 Silver"),
    (0,    "🥉 Bronze"),
]


def get_rank(elo: int) -> str:
    """Return the rank label (with emoji) that corresponds to *elo*.

    Tiers (ascending):
        Bronze     –   0 – 699
        Silver     – 700 – 999
        Gold       – 1 000 – 1 199
        Platinum   – 1 200 – 1 499
        Diamond    – 1 500 – 1 799
        Legendary  – 1 800 – 2 099
        Challenger – 2 100 +

    Negative ELO (should not occur in practice) maps to Bronze.
    """
    for threshold, label in _RANKS:
        if elo >= threshold:
            return label
    # Defensive: negative ELO falls back to the lowest tier
    return "🥉 Bronze"
