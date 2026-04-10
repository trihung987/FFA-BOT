"""
Leaderboard slash commands.
"""
import logging
import discord
from discord.ext import commands as ext_commands
from typing import Optional

from config import GUILD_ID
from entity import User, Lobby
from helpers import get_rank, now_vn, safe_send_interaction
from sqlalchemy import text as sa_text

log = logging.getLogger(__name__)
guild_obj = discord.Object(id=GUILD_ID)

# ── Rank display config ────────────────────────────────────────────────────────

RANK_ICON = {
    "Challenger": "★",
    "Legendary":  "◆",
    "Diamond":    "♦",
    "Platinum":   "●",
    "Gold":       "▲",
    "Silver":     "▶",
    "Bronze":     "▼",
}

# ANSI foreground colors for Discord ```ansi``` code blocks.
# 31 red, 32 green, 33 yellow, 34 blue, 35 magenta, 36 cyan, 37 white, 91 light red.
RANK_ANSI = {
    "Challenger": "1;31",
    "Legendary":  "1;35",
    "Diamond":    "1;34",
    "Platinum":   "1;36",
    "Gold":       "1;33",
    "Silver":     "1;37",
    "Bronze":     "1;37",
}

# Foreground-only ANSI so the background stays unchanged.
# Border: yellow text
# Header/title: standard green text
BORDER_ANSI = "32"
HEADER_ANSI = "1;32"  

# ── Column widths (number of visible characters, not bytes) ───────────────────
W_POS     = 2   # #
W_NAME    = 14  # Người chơi
W_ELO     = 4   # ELO
W_TIER    = 12  # Tier (icon + name)
W_MATCHES = 5   # Tổng trận
W_DELTA   = 7   # Biến động
W_MONTHLY = 8   # ELO tháng

# ── Low-level helpers ──────────────────────────────────────────────────────────

def _rank_key(elo: int) -> str:
    """Return just the rank word, e.g. 'Diamond'."""
    return get_rank(elo).split()[-1]


def _fmt_delta(value: int) -> str:
    if value > 0:
        return f"▲ +{value}"
    if value < 0:
        return f"▼ {value}"
    return "• 0"


def _trend_color(value: int) -> str:
    """Color for delta/monthly columns: up green, down light red, neutral white."""
    if value > 0:
        return "1;42"
    if value < 0:
        return "1;31"
    return "1;37"


def _cell(text: str, width: int, align: str = "left") -> str:
    """
    Fit *text* into exactly *width* ASCII-safe characters.
    Vietnamese letters are single-width in most monospace fonts used by
    Discord on desktop; we treat every character as width-1 here.
    If the string is longer than *width* it is truncated with '…'.
    """
    text = str(text)
    if len(text) > width:
        text = text[: width - 1] + "…"
    if align == "right":
        return text.rjust(width)
    if align == "center":
        return text.center(width)
    return text.ljust(width)


def _ansi(text: str, code: str) -> str:
    """Wrap *text* with ANSI escape code for Discord ansi code blocks."""
    return f"\u001b[{code}m{text}\u001b[0m"


# ── Border helpers ─────────────────────────────────────────────────────────────

# Each segment width = column width + 2 (one space padding on each side)
_SEGS = [W_POS, W_NAME, W_ELO, W_TIER, W_MATCHES, W_DELTA, W_MONTHLY]


def _hline(left: str, sep: str, right: str, fill: str = "═") -> str:
    parts = [fill * (w + 2) for w in _SEGS]
    return _ansi(left + sep.join(parts) + right, BORDER_ANSI)


def _border_char(char: str) -> str:
    return _ansi(char, BORDER_ANSI)


def _row(
    *cells,
    aligns: Optional[list[str]] = None,
    cell_color: Optional[str] = None,
    color_border: bool = True,
) -> str:
    """
    Build one table row.  *cells* must be pre-padded strings in column order:
    pos, name, elo, tier, matches, delta, monthly.
    """
    widths = _SEGS
    row_aligns = aligns or ["right", "left", "right", "left", "right", "right", "right"]
    parts = []
    for value, width, align in zip(cells, widths, row_aligns):
        text = _cell(value, width, align)
        if cell_color is not None:
            text = _ansi(text, cell_color)
        parts.append(f" {text} ")
    if color_border:
        border = _border_char("║")
    else:
        border = "║"
    return border + border.join(parts) + border


def _row_with_colored_tier(
    pos: str,
    name: str,
    elo: str,
    tier_plain: str,
    tier_ansi_code: str,
    matches: str,
    last_elo_change: int,
    monthly_elo_gain: int,
) -> str:
    """Build one row with tier colors and value-based colors for trend columns."""
    delta_color = _trend_color(last_elo_change)
    monthly_color = _trend_color(monthly_elo_gain)
    delta = _fmt_delta(last_elo_change)
    monthly = _fmt_delta(monthly_elo_gain)

    parts = [
        f" {_ansi(_cell(pos, W_POS, 'right'), tier_ansi_code)} ",
        f" {_ansi(_cell(name, W_NAME, 'left'), tier_ansi_code)} ",
        f" {_ansi(_cell(elo, W_ELO, 'right'), tier_ansi_code)} ",
        f" {_ansi(_cell(tier_plain, W_TIER, 'left'), tier_ansi_code)} ",
        f" {_ansi(_cell(matches, W_MATCHES, 'right'), tier_ansi_code)} ",
        f" {_ansi(_cell(delta, W_DELTA, 'right'), delta_color)} ",
        f" {_ansi(_cell(monthly, W_MONTHLY, 'right'), monthly_color)} ",
    ]
    return _border_char("║") + _border_char("║").join(parts) + _border_char("║")


# ── Table builder ──────────────────────────────────────────────────────────────

def _build_table(rows: list[dict]) -> str:
    """
    Assemble the full ASCII table.

    Each dict must have:
        pos              int
        name             str
        elo              int
        last_elo_change  int
        monthly_elo_gain int
        total_matches    int
    """
    # Title spans near full width; keep 1-char shorter to fix right-border drift.
    inner = sum(w + 2 for w in _SEGS) + len(_SEGS) - 1
    title_plain = "★ BẢNG XẾP HẠNG ELO FFA ★"
    title_centered = _cell(title_plain, inner - 1, "center")
    title_row = _border_char("║") + _ansi(title_centered, HEADER_ANSI) + _border_char("║")

    header_aligns = ["center", "center", "center", "center", "center", "center", "center"]
    header_row_1 = _row(
        "#", "Người chơi", "ELO", "Tier", "Tổng", "Biến", "ELO",
        aligns=header_aligns,
        cell_color=HEADER_ANSI,
        color_border=True,
    )
    header_row_2 = _row(
        "", "", "", "", "trận", "động", "tháng",
        aligns=header_aligns,
        cell_color=HEADER_ANSI,
        color_border=True,
    )

    lines = [
        _hline("╔", "╦", "╗"),
        title_row,
        _hline("╠", "╦", "╣"),
        header_row_1,
        header_row_2,
        _hline("╠", "╬", "╣"),
    ]

    for r in rows:
        key  = _rank_key(r["elo"])
        icon = RANK_ICON.get(key, "?")
        tier = f"{icon} {key}"
        tier_color = RANK_ANSI.get(key, "37")

        lines.append(
            _row_with_colored_tier(
                f"#{r['pos']}",
                r["name"],
                str(r["elo"]),
                tier,
                tier_color,
                str(r["total_matches"]),
                int(r["last_elo_change"]),
                int(r["monthly_elo_gain"]),
            )
        )

    lines.append(_hline("╚", "╩", "╝"))
    lines.append(_ansi("★ Challenger", "31") + "  " + _ansi("◆ Legendary", "35") + "  " + _ansi("♦ Diamond", "34") + "  " + _ansi("● Platinum", "36") + "  " + _ansi("▲ Gold", "33") + "  " + _ansi("▶ Silver", "37") + "  " + _ansi("▼ Bronze", "37"))
    return "\n".join(lines)


# ── Command registration ───────────────────────────────────────────────────────

def register_leaderboard_commands(bot: ext_commands.Bot, db_session_factory) -> None:
    """Attach leaderboard-related slash commands to *bot*."""

    @bot.tree.command(
        name="ffa_leaderboard",
        description="Hiển thị bảng xếp hạng ELO.",
        guild=guild_obj,
    )
    async def leaderboard(interaction: discord.Interaction) -> None:

        # ── 1. Fetch data ──────────────────────────────────────────────────────
        try:
            with db_session_factory() as session:
                top_users: list[User] = (
                    session.query(User)
                    .order_by(User.elo.desc())
                    .limit(10)
                    .all()
                )

                user_ids = [u.id for u in top_users]

                # Count finished lobbies each user participated in.
                # users_list is a JSONB array of Discord user IDs (integers).
                match_counts_raw = session.execute(
                    sa_text("""
                        SELECT elem::bigint AS user_id, COUNT(*) AS cnt
                        FROM   lobbies,
                               jsonb_array_elements_text(users_list) AS elem
                        WHERE  status = 'finished'
                          AND  elem::bigint = ANY(:ids)
                        GROUP  BY elem::bigint
                    """),
                    {"ids": user_ids},
                ).fetchall()

                match_count_map: dict[int, int] = {
                    row.user_id: row.cnt for row in match_counts_raw
                }

        except Exception:
            log.exception("DB error in leaderboard (user=%s)", interaction.user.id)
            await safe_send_interaction(
                interaction,
                "leaderboard",
                "❌ Đã xảy ra lỗi nội bộ khi truy vấn dữ liệu.",
                ephemeral=True,
            )
            return

        if not top_users:
            await safe_send_interaction(
                interaction,
                "leaderboard",
                "Chưa có người dùng nào trong hệ thống.",
                ephemeral=True,
            )
            return

        # ── 2. Resolve display names ───────────────────────────────────────────
        rows = []
        now = now_vn()
        current_month = (now.year, now.month)
        for rank, user in enumerate(top_users, start=1):
            member = interaction.guild.get_member(user.id)
            name   = member.display_name if member else f"User{user.id}"
            update_month = (
                (user.updated_date.year, user.updated_date.month)
                if user.updated_date is not None
                else None
            )
            monthly_value = user.monthly_elo_gain if update_month == current_month else 0
            rows.append({
                "pos":              rank,
                "name":             name,
                "elo":              user.elo,
                "last_elo_change":  user.last_elo_change,
                "monthly_elo_gain": monthly_value,
                "total_matches":    match_count_map.get(user.id, 0),
            })

        # ── 3. Send as plain code block (no embed) ─────────────────────────────
        # Plain ``` blocks preserve monospace alignment in Discord on all
        # platforms.  Embed descriptions do NOT guarantee monospace rendering.
        table   = _build_table(rows)
        content = f"```ansi\n{table}\n```"

        await safe_send_interaction(interaction, "leaderboard", content)

    @bot.tree.command(
        name="ansi_256_test",
        description="Test toàn bộ mã màu ANSI từ 0 đến 255.",
        guild=guild_obj,
    )
    async def ansi_256_test(interaction: discord.Interaction) -> None:
        """Show ANSI 256-color preview in multiple pages."""
        lines_per_page = 20
        lines: list[str] = []

        for code in range(256):
            label = f"{code:>3}"
            sample = f"\u001b[38;5;{code}mFG\u001b[0m / \u001b[48;5;{code}m  BG  \u001b[0m"
            lines.append(f"{label}: {sample}")

        pages: list[str] = []
        for i in range(0, len(lines), lines_per_page):
            chunk = "\n".join(lines[i:i + lines_per_page])
            page_no = (i // lines_per_page) + 1
            total = (len(lines) + lines_per_page - 1) // lines_per_page
            pages.append(f"[{page_no}/{total}]\n```ansi\n{chunk}\n```")

        try:
            await interaction.response.send_message(pages[0], ephemeral=True)
            for page in pages[1:]:
                await interaction.followup.send(page, ephemeral=True)
        except Exception:
            log.exception("ansi_256_test failed (user=%s)", interaction.user.id)