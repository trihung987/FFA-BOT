"""
Lobby division logic for FFA matches.

Triggered when the lobby-division time is reached (end of check-in window).
Handles:
 - Splitting checked-in players into has-ticket / no-ticket groups
 - ELO-based eligibility filtering
 - Tiered lobby assignment (Huyền Thoại → Chinh Phạt → Kim Cương / Tân Binh)
 - AI slot filling when a lobby is short by 1–2 players
 - Civ (civilization) assignment per fight with no intra-lobby duplicates
 - Voice + text channel creation (one pair per fight, lobby-private)
 - Posting display and result-entry embeds
"""

from __future__ import annotations

from io import BytesIO
import logging
from pathlib import Path
import random
import re
from urllib.request import urlopen
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from discord.ext import commands as ext_commands

log = logging.getLogger(__name__)

# Bundled fonts (ship with repo) so Pillow rendering is stable on servers.
FONT_BASE_DIR = Path(__file__).resolve().parent / "Be_Vietnam_Pro,Noto_Sans,Poppins"
BE_VIETNAM_PRO_DIR = FONT_BASE_DIR / "Be_Vietnam_Pro"
NOTO_SANS_DIR = FONT_BASE_DIR / "Noto_Sans" / "static"

# ── Civilization pool ──────────────────────────────────────────────────────────
# Each entry is a Discord custom-emoji string, e.g. "<:civ_anh:1234567890>".
# Leave as empty strings "" – admin fills in the actual emoji codes before deploy.
CIVS: list[str] = [
    ":abbasid_dynasty:",  # civ 1
    ":ayyubids:",  # civ 2
    ":byzantines:",  # civ 3
    ":chinese:",  # civ 4
    ":delhi_sultanate:",  # civ 5
    ":english:",  # civ 6
    ":french:",  # civ 7
    ":golden_horde:",  # civ 8
    ":holy_roman_empire:",  # civ 9
    ":house_of_lancaster:",  # civ 10
    ":japanese:",  # civ 11
    ":jeanne_darc:",  # civ 12
    ":knights_templar:",  # civ 13
    ":macedonian_dynasty:",  # civ 14
    ":malians:",  # civ 15
    ":mongols:",  # civ 16
    ":order_of_the_dragon:",  # civ 17
    ":ottomans:",  # civ 18
    ":rus:",  # civ 19
    ":sengoku_daimyo:",  # civ 20
    ":tughlaq_dynasty:",  # civ 21
    ":zhu_xis_legacy:"  # civ 22

]

# ── Lobby tier constants ───────────────────────────────────────────────────────
TIER_LEGENDARY = "Huyền Thoại"
TIER_CONQUEST = "Chinh Phạt"
TIER_DIAMOND = "Kim Cương"
TIER_RECRUIT = "Tân Binh"

TIER_EMOJI: dict[str, str] = {
    TIER_LEGENDARY: "🏆",
    TIER_CONQUEST: "⚔️",
    TIER_DIAMOND: "💎",
    TIER_RECRUIT: "🎖️",
}

MAX_LOBBY_SIZE = 8
MIN_LOBBY_SIZE = 6          # fewer than this → cancel the lobby
MAX_AI_COUNT = MAX_LOBBY_SIZE - MIN_LOBBY_SIZE  # 2


# ── Civ assignment ─────────────────────────────────────────────────────────────

def assign_civs(all_keys: list[str], count_fight: int) -> dict[str, list[str]]:
    """Assign one unique civ per fight to each player/AI key.

    Within a single fight no two entries share the same civ slot (sampled without
    replacement from :data:`CIVS`).  The same civ *may* appear for the same
    player across different fights.

    Parameters
    ----------
    all_keys:
        String identifiers for every slot – either ``str(user_id)`` or
        ``"AI_1"``, ``"AI_2"``, etc.
    count_fight:
        Number of fights to assign civs for.

    Returns
    -------
    dict[str, list[str]]
        Mapping of key → list of civ emoji strings, one per fight.
    """
    if all(c == "" for c in CIVS):
        log.warning(
            "CIVS list contains only empty strings – fill in Discord custom emoji "
            "codes in lobby_division.py before deploying."
        )

    n = len(all_keys)
    if n > len(CIVS):
        raise ValueError(f"Not enough civs ({len(CIVS)}) for {n} players")

    result: dict[str, list[str]] = {k: [] for k in all_keys}
    for _ in range(count_fight):
        picked = random.sample(CIVS, n)
        for key, civ in zip(all_keys, picked):
            result[key].append(civ)
    return result


# ── Civ emoji helpers ──────────────────────────────────────────────────────────


def _build_emoji_map(guild: discord.Guild) -> dict[str, str]:
    """Build a ``{emoji_name: discord_emoji_str}`` map from the guild's custom emojis."""
    return {e.name: str(e) for e in guild.emojis}


def _resolve_emoji_str(s: str, emoji_map: dict[str, str]) -> str:
    """Resolve a ``:name:`` style string to ``<:name:id>`` using the guild emoji map.

    Strings that are already in ``<:name:id>`` or ``<a:name:id>`` format are
    returned unchanged.  Unknown names are also returned as-is.
    """
    if s.startswith("<:") or s.startswith("<a:"):
        return s  # already fully-qualified
    if s.startswith(":") and s.endswith(":") and len(s) > 2:
        name = s[1:-1]
        if name in emoji_map:
            return emoji_map[name]
    return s


def _civ_display_name(civ_str: str) -> str:
    """Extract a human-readable civ name from a Discord emoji string.

    Examples::

        "<:abbasid_dynasty:123456>"  →  "Abbasid Dynasty"
        ":abbasid_dynasty:"          →  "Abbasid Dynasty"
        "🎮"                         →  "🎮"
    """
    if civ_str.startswith("<:") and ":" in civ_str[2:]:
        raw = civ_str[2:].split(":")[0]
        return raw.replace("_", " ").title()
    if civ_str.startswith(":") and civ_str.endswith(":") and len(civ_str) > 2:
        raw = civ_str[1:-1]
        return raw.replace("_", " ").title()
    return civ_str



# ── Lobby display message builder ─────────────────────────────────────────────


def build_lobby_display_notice_lines(lobby, match) -> list[str]:
    """Build shared notice lines for lobby display messages."""
    ordered_maps = [name for name in (match.name_maps or []) if name]
    map_order_line = (
        "🗺️ **Map theo thứ tự:** "
        + " | ".join(f"T{i}: {name}" for i, name in enumerate(ordered_maps, start=1))
    ) if ordered_maps else None

    civ_notice = (
        "> 🎯 *Các civs đã được phân bổ ngẫu nhiên theo danh sách dưới*"
        if getattr(lobby, "civs", None)
        else "🕒 Chưa có thông tin chọn tướng"
    )

    lines = [line for line in (map_order_line, civ_notice) if line]
    return lines


def _build_lobby_display_image_filename(lobby, match) -> str:
    return f"lobby_{match.id}_{lobby.id}.png"


def build_lobby_display_embed(lobby, match) -> discord.Embed:
    """Build embed metadata for a lobby-division image message."""
    tier = lobby.tier or ""
    tier_icon = TIER_EMOJI.get(tier, "🎮")
    notice_lines = build_lobby_display_notice_lines(lobby, match)

    details = [
        f"🆔 **ID Lobby:** #{lobby.id}",
        f"🎯 **Tier:** {tier}",
        f"👥 **Số người chơi:** {len(lobby.users_list or [])}",
    ]
    details.extend(notice_lines)

    embed = discord.Embed(
        title=f"{tier_icon} Chia Lobby - Trận #{match.id} | {tier} #{lobby.lobby_number}",
        description="\n".join(details),
        color=discord.Color.blue(),
    )
    embed.set_image(url=f"attachment://{_build_lobby_display_image_filename(lobby, match)}")
    embed.set_footer(text=f"Lobby ID: {lobby.id} | Trận #{match.id}")
    return embed


def build_lobby_display_messages(
    lobby,
    match,
    p_map: dict[int, str],
) -> list[str]:
    """Build one or more plain-text lobby display messages with a fixed-width civ table.

    The output is chunked so each message stays under Discord's 2000 character
    limit while preserving emoji rendering in normal message content.
    """
    tier = lobby.tier
    tier_icon = TIER_EMOJI.get(tier, "🎮")
    title = f"{tier_icon} **Trận #{match.id} | Lobby {tier} #{lobby.lobby_number}**"
    notice_lines = build_lobby_display_notice_lines(lobby, match)

    count_fight: int = match.count_fight
    civs: dict = lobby.civs or {}
    users_list: list = lobby.users_list or []
    ai_count: int = lobby.ai_count or 0

    player_entries: list[tuple[str, str]] = [
        (p_map.get(uid, "Unknown"), str(uid)) for uid in users_list
    ]
    for idx in range(1, ai_count + 1):
        player_entries.append(("AI", f"AI_{idx}"))

    if not player_entries:
        return [f"{title}\n_(chưa có người chơi)_"]

    # Keep the name column fixed-width via inline-code padding. This gives a
    # stable left column while still allowing civ emojis to render normally.
    name_col_width = 15

    def _short_name(name: str) -> str:
        return name if len(name) <= name_col_width else f"{name[:name_col_width - 1]}…"

    def _code_cell(name: str) -> str:
        return f"`{_short_name(name).ljust(name_col_width)}`"

    header = f"{_code_cell('NGƯỜI CHƠI')} | " + " | ".join(f"**T{i}**" for i in range(1, count_fight + 1))
    divider = f"{'-' * (name_col_width + 2)}|" + "|".join("---" for _ in range(count_fight))

    row_lines: list[str] = []
    for name, key in player_entries:
        player_civs = civs.get(key, [])
        civ_tokens = [player_civs[i] if i < len(player_civs) else "—" for i in range(count_fight)]
        row_lines.append(f"{_code_cell(name)} | " + " | ".join(civ_tokens))

    # Try to send in one message first.
    single_message = "\n".join([title, *notice_lines, header, divider, *row_lines])
    if len(single_message) <= 2000:
        return [single_message]

    # Fallback: chunk rows without repeating the title every time.
    max_chars = 1950
    messages: list[str] = []
    current_lines: list[str] = [title, *notice_lines, header, divider]

    def flush_current() -> None:
        if len(current_lines) > 3:
            messages.append("\n".join(current_lines))

    for row in row_lines:
        candidate = "\n".join(current_lines + [row])
        if len(candidate) > max_chars:
            flush_current()
            current_lines = [header, divider, row]
        else:
            current_lines.append(row)

    if len(current_lines) > 2:
        messages.append("\n".join(current_lines))

    return messages


async def build_lobby_display_image_file(
    lobby,
    match,
    p_map: dict[int, str],
) -> discord.File | None:
    """Render a lobby civ table image and return it as a Discord file.

    Returns ``None`` when Pillow is unavailable or image rendering fails.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps
    except Exception:
        log.warning("Pillow not installed; falling back to text lobby table")
        return None

    count_fight: int = match.count_fight
    civs: dict = lobby.civs or {}
    users_list: list = lobby.users_list or []
    ai_count: int = lobby.ai_count or 0

    player_entries: list[tuple[str, str]] = [
        (p_map.get(uid, "Unknown"), str(uid)) for uid in users_list
    ]
    for idx in range(1, ai_count + 1):
        player_entries.append(("AI", f"AI_{idx}"))

    if not player_entries:
        return None

    # Layout constants
    pad = 36
    name_col_w = 330
    civ_col_w = 104
    title_h = 96
    subtitle_h = 36
    header_h = 58
    row_h = 54
    fights_w = count_fight * civ_col_w
    width = pad * 2 + name_col_w + fights_w
    height = pad * 2 + title_h + subtitle_h + header_h + len(player_entries) * row_h

    # Tier-based colors
    tier_colors = {
        TIER_LEGENDARY: {
            "header_bg": (184, 134, 11, 245),  # Gold
            "header_text": (255, 255, 255),
            "row_alt_light": (32, 32, 32, 230),
            "row_alt_dark": (42, 42, 42, 230),
        },
        TIER_CONQUEST: {
            "header_bg": (220, 20, 60, 245),  # Crimson
            "header_text": (255, 255, 255),
            "row_alt_light": (32, 32, 32, 230),
            "row_alt_dark": (42, 42, 42, 230),
        },
        TIER_DIAMOND: {
            "header_bg": (64, 180, 233, 245),  # Light Blue
            "header_text": (255, 255, 255),
            "row_alt_light": (32, 32, 32, 230),
            "row_alt_dark": (42, 42, 42, 230),
        },
        TIER_RECRUIT: {
            "header_bg": (60, 140, 60, 245),  # Green
            "header_text": (255, 255, 255),
            "row_alt_light": (32, 32, 32, 230),
            "row_alt_dark": (42, 42, 42, 230),
        },
    }
    
    tier_color = tier_colors.get(lobby.tier, tier_colors[TIER_RECRUIT])

    # Colors
    bg = (18, 20, 24)
    panel_bg = (12, 16, 22, 190)
    table_bg = (24, 28, 34, 235)
    header_bg = tier_color["header_bg"]
    row_light = (40, 45, 55, 205)
    row_dark = (31, 36, 46, 205)
    border = (124, 136, 158, 255)
    border_light = (92, 103, 122, 210)
    text_main = (240, 243, 250)
    text_sub = (214, 220, 232)

    background_path = (
        Path(__file__).resolve().parent
        / "background"
        / "AoEIV-DynastiesOfTheEast-WatchYourSteppe-1920x1080-1.webp"
    )
    try:
        if background_path.exists():
            background_img = Image.open(background_path).convert("RGBA")
            img = ImageOps.fit(background_img, (width, height), method=Image.LANCZOS)
        else:
            img = Image.new("RGBA", (width, height), bg)
    except Exception:
        img = Image.new("RGBA", (width, height), bg)

    # Apply subtle overlay for better contrast
    overlay = Image.new("RGBA", (width, height), (8, 10, 14, 138))
    img.alpha_composite(overlay)
    draw = ImageDraw.Draw(img)

    def _load_font(size: int, *, bold: bool = False):
        candidates = [
            BE_VIETNAM_PRO_DIR / ("BeVietnamPro-Bold.ttf" if bold else "BeVietnamPro-Regular.ttf"),
            NOTO_SANS_DIR / ("NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf"),
        ]
        for path in candidates:
            if path.exists():
                try:
                    return ImageFont.truetype(str(path), size)
                except Exception:
                    continue
        return ImageFont.load_default()

    font_title = _load_font(34, bold=True)
    font_subtitle = _load_font(19)
    font_header = _load_font(22)
    font_header_bold = _load_font(22, bold=True)
    font_cell = _load_font(20)

    table_x = pad
    table_y = pad + title_h + subtitle_h
    table_w = name_col_w + fights_w
    table_h = header_h + len(player_entries) * row_h

    # Draw top panel + title/subtitle.
    panel_top = max(10, pad - 8)
    panel_bottom = table_y + table_h + 10
    draw.rounded_rectangle(
        (pad - 10, panel_top, width - pad + 10, panel_bottom),
        radius=22,
        fill=panel_bg,
        outline=(152, 164, 186, 225),
        width=2,
    )

    title_text = f"Bảng chia civ - Lobby {lobby.tier} #{lobby.lobby_number}"
    subtitle_text = f"Trận #{match.id} | Số trận: {count_fight}"
    title_w = draw.textlength(title_text, font=font_title)
    subtitle_w = draw.textlength(subtitle_text, font=font_subtitle)
    draw.text(((width - title_w) / 2, pad + 2), title_text, font=font_title, fill=text_main)
    draw.text(((width - subtitle_w) / 2, pad + 47), subtitle_text, font=font_subtitle, fill=text_sub)

    # Draw table shell.
    draw.rectangle((table_x, table_y, table_x + table_w, table_y + table_h), fill=table_bg, outline=border, width=3)
    
    # Draw header with tier color
    draw.rectangle((table_x, table_y, table_x + table_w, table_y + header_h), fill=header_bg)
    
    # Divider line below header (stronger)
    draw.line((table_x, table_y + header_h, table_x + table_w, table_y + header_h), fill=border, width=3)

    # Vertical grid lines - main divider between player names and civs
    draw.line((table_x + name_col_w, table_y, table_x + name_col_w, table_y + table_h), fill=border, width=2)
    for i in range(1, count_fight):
        x = table_x + name_col_w + i * civ_col_w
        draw.line((x, table_y, x, table_y + table_h), fill=border_light, width=1)

    # Draw alternating row backgrounds
    for idx in range(len(player_entries)):
        y = table_y + header_h + idx * row_h
        row_color = row_light if idx % 2 == 0 else row_dark
        draw.rectangle((table_x, y, table_x + table_w, y + row_h), fill=row_color)

    # Headers with better styling
    draw.text((table_x + 16, table_y + 14), "NGƯỜI CHƠI", font=font_header_bold, fill=tier_color["header_text"])
    for i in range(1, count_fight + 1):
        cx = table_x + name_col_w + (i - 1) * civ_col_w + civ_col_w // 2
        label = f"T{i}"
        tw = draw.textlength(label, font=font_header_bold)
        draw.text((cx - tw / 2, table_y + 14), label, font=font_header_bold, fill=tier_color["header_text"])

    emoji_full_re = re.compile(r"^<(?P<anim>a?):(?P<name>[^:]+):(?P<id>\d+)>$")

    flags_dir = Path(__file__).resolve().parent / "flags"
    icon_cache: dict[str, Image.Image] = {}

    def _fetch_emoji_icon_from_cdn(emoji_id: str) -> Image.Image | None:
        cache_key = f"cdn:{emoji_id}"
        if cache_key in icon_cache:
            return icon_cache[cache_key]
        for ext in ("png", "webp"):
            url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}?size=64&quality=lossless"
            try:
                with urlopen(url, timeout=8) as resp:
                    raw = resp.read()
                icon = Image.open(BytesIO(raw)).convert("RGBA")
                icon_cache[cache_key] = icon
                return icon
            except Exception:
                continue
        return None

    def _extract_civ_name_and_id(civ_raw: str) -> tuple[str | None, str | None]:
        full = emoji_full_re.match(civ_raw)
        if full:
            return full.group("name"), full.group("id")
        if civ_raw.startswith(":") and civ_raw.endswith(":") and len(civ_raw) > 2:
            return civ_raw[1:-1], None
        return None, None

    def _load_local_flag_icon(civ_name: str) -> Image.Image | None:
        cache_key = f"local:{civ_name}"
        if cache_key in icon_cache:
            return icon_cache[cache_key]

        for ext in ("webp", "png", "jpg", "jpeg"):
            path = flags_dir / f"{civ_name}.{ext}"
            if path.exists():
                try:
                    icon = Image.open(path).convert("RGBA")
                    icon_cache[cache_key] = icon
                    return icon
                except Exception:
                    continue
        return None

    for row_idx, (name, key) in enumerate(player_entries):
        y_top = table_y + header_h + row_idx * row_h
        y_center = y_top + row_h // 2

        # Add subtle name column background
        draw.rectangle((table_x, y_top, table_x + name_col_w, y_top + row_h), 
                      fill=table_bg)

        disp_name = name if len(name) <= 24 else f"{name[:23]}…"
        draw.text((table_x + 16, y_center - 11), disp_name, font=font_cell, fill=text_main)

        player_civs = civs.get(key, [])
        for i in range(count_fight):
            civ_raw = player_civs[i] if i < len(player_civs) else "—"
            x_left = table_x + name_col_w + i * civ_col_w
            cx = x_left + civ_col_w // 2

            icon = None
            if isinstance(civ_raw, str):
                civ_name, emoji_id = _extract_civ_name_and_id(civ_raw)
                if civ_name:
                    # Prefer local files for speed and stability.
                    icon = _load_local_flag_icon(civ_name)
                if icon is None and emoji_id:
                    icon = _fetch_emoji_icon_from_cdn(emoji_id)

            if icon is not None:
                max_w = int(civ_col_w * 0.75)
                max_h = int(row_h * 0.75)
                scale = min(max_w / icon.width, max_h / icon.height)
                new_w = max(1, int(icon.width * scale))
                new_h = max(1, int(icon.height * scale))
                icon_resized = icon.resize((new_w, new_h), Image.LANCZOS)
                img.alpha_composite(icon_resized, (int(cx - new_w / 2), int(y_center - new_h / 2)))
                continue

            text = civ_raw if isinstance(civ_raw, str) and civ_raw else "-"
            tw = draw.textlength(text, font=font_cell)
            draw.text((cx - tw / 2, y_center - 11), text, font=font_cell, fill=text_sub)

    # Draw horizontal borders between players on top (so they're visible across all columns)
    for idx in range(1, len(player_entries) + 1):
        y = table_y + header_h + idx * row_h
        draw.line((table_x, y, table_x + table_w, y), fill=border, width=2)

    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return discord.File(fp=output, filename=_build_lobby_display_image_filename(lobby, match))


# ── Discord channel creation ───────────────────────────────────────────────────

async def create_lobby_channels(
    guild: discord.Guild,
    lobby,
    match,
    p_map: dict[int, str],
    category: discord.CategoryChannel | None,
    judge_role: discord.Role | None,
) -> tuple[list[int], list[int]]:
    """Create one voice channel per lobby and one text channel per fight.

    Channels are visible only to players in the lobby, admins, and holders of
    *judge_role* (if provided).

    Returns
    -------
    (voice_channel_ids, text_channel_ids)
        Voice list contains a single lobby voice channel ID.
        Text list contains one channel ID per fight (0-based order).
    """
    # Permission overwrites: 
    voice_overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),
    }
    text_overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }
    if judge_role:
        voice_overwrites[judge_role] = discord.PermissionOverwrite(
            view_channel=True, connect=True, send_messages=True
        )
        text_overwrites[judge_role] = discord.PermissionOverwrite(
            view_channel=True, connect=True, send_messages=True
        )
    for uid in (lobby.users_list or []):
        member = guild.get_member(uid)
        if member:
            voice_overwrites[member] = discord.PermissionOverwrite(
                view_channel=True, connect=True, send_messages=True
            )
            text_overwrites[member] = discord.PermissionOverwrite(
                view_channel=True, connect=True, send_messages=True
            )

    tier_full_slug = {
        TIER_LEGENDARY: "huyền-thoại",
        TIER_CONQUEST: "chinh-phạt",
        TIER_DIAMOND: "kim-cương",
        TIER_RECRUIT: "tân-binh",
    }.get(lobby.tier, "lobby")
    map_names: list = match.name_maps or []

    voice_ids: list[int] = []
    text_ids: list[int] = []

    # One shared voice room for the whole lobby.
    vc = await guild.create_voice_channel(
        name=f"🎮 Trận#{match.id} Lobby {lobby.tier} #{lobby.lobby_number}",
        overwrites=voice_overwrites,
        category=category,
    )
    voice_ids.append(vc.id)

    for i in range(1, match.count_fight + 1):
        map_name = map_names[i - 1] if i - 1 < len(map_names) else f"map{i}"

        tc = await guild.create_text_channel(
            name=f"Khai-báo-trận-{match.id}-{tier_full_slug}-{lobby.lobby_number}-ván-{i}",
            overwrites=text_overwrites,
            category=category,
        )
        text_ids.append(tc.id)
        try:
            await tc.send(
                "@here\n"
                f"**[Trận #{match.id}] Lobby {tier_full_slug.upper()} - Kênh chat này dùng để khai báo điểm số Ván {i} - Map {map_name}.**\n"
                "**Mỗi người tự Khai báo điểm (Vua/Bể landmark trước/Firstblood) ăn được tại đây để admin/trọng tài kiểm tra và nhập kết quả.**\n"
                "⚠️ **Nếu có hành vi không đúng quy tắc hoặc tranh chấp, vui lòng tag @Ban tổ chức hoặc @Trọng tài để xử lý.**"
            )
        except discord.HTTPException:
            log.warning(
                "create_lobby_channels: failed to send score-note message (lobby=%s, fight=%s)",
                getattr(lobby, "id", None),
                i,
            )

    return voice_ids, text_ids


# ── Main division entry point ──────────────────────────────────────────────────

async def divide_lobbies(
    bot: "ext_commands.Bot",
    match,
    db_session_factory,
) -> None:
    """Divide checked-in players into tiered lobbies.

    Steps
    -----
    1. Split players into *has_ticket_group* / *no_ticket_group*.
       Deduct 1 ticket from every player in has_ticket_group.
    2. Sort both groups by ELO descending.
    3. Remove any no-ticket player whose ELO > lowest ELO in has_ticket_group
       (they form *cannot_join_group* and are announced but not placed).
    4. Validate minimum group size (≥ 6).
    5. Divide has_ticket_group into:
       - Lobby 1 → Huyền Thoại (first 8)
       - Lobby 2 → Chinh Phạt  (next 8)
       - Lobbies 3+ → Kim Cương (remaining, groups of 8)
       For each lobby: if < 6 → cancel; if 6–7 → add AI slots to reach 8.
    6. Divide no_ticket_group into Tân Binh lobbies (groups of 8, same AI rule).
    7. For each valid lobby: assign civs, persist to DB, create channels.
    8. Send display embed to announce channel.
    9. Send result-entry embed (with score/cancel/finalize buttons) to result channel.
    """
    from entity import Match, User, Lobby
    from config import (
        DIVIDE_LOBBY_CHANNEL_ID,
        JUDGE_ROLE_ID,
        LOBBY_CATEGORY_ID,
    )
    from helpers import now_vn

    announce_channel = bot.get_channel(DIVIDE_LOBBY_CHANNEL_ID)
    async def _announce(*args, **kwargs):
        if not announce_channel:
            return None
        msg = await announce_channel.send(*args, **kwargs)
        from scheduler import track_divide_message

        track_divide_message(match.id, msg.id)
        return msg

    async def _send_lobby_display(*args, **kwargs):
        if not announce_channel:
            return None
        return await announce_channel.send(*args, **kwargs)

    # Guard: skip if lobbies have already been created for this match
    # (prevents double-execution when the scheduler ticks twice within the same minute)
    with db_session_factory() as session:
        existing_count = session.query(Lobby).filter(Lobby.match_id == match.id).count()
    if existing_count > 0:
        return

    checkin_ids: list = match.checkin_users_id or []
    if not checkin_ids:
        if announce_channel:
            await _announce(
                f"⚠️ **Trận #{match.id}** – Không có người chơi nào check-in, "
                "không thể chia lobby."
            )
        with db_session_factory() as session:
            db_match = session.get(Match, match.id)
            if db_match is not None:
                db_match.status = "cancelled"
                if db_match.end_time is None:
                    db_match.end_time = now_vn()
                session.commit()
        return

    # ── Step 1 & 2: Split + sort ───────────────────────────────────────────────
    has_ticket_group: list[tuple[int, int]] = []   # (user_id, elo)
    no_ticket_group: list[tuple[int, int]] = []

    with db_session_factory() as session:
        users = session.query(User).filter(User.id.in_(checkin_ids)).all()
        user_map = {u.id: u for u in users}

        for uid in checkin_ids:
            user = user_map.get(uid)
            if user is None:
                continue
            elo = user.elo or 0
            if user.ticket > 0:
                has_ticket_group.append((uid, elo))
                user.ticket -= 1
            else:
                no_ticket_group.append((uid, elo))

        session.commit()

    has_ticket_group.sort(key=lambda x: x[1], reverse=True)
    no_ticket_group.sort(key=lambda x: x[1], reverse=True)

    if announce_channel:
        def _fmt_player(uid: int) -> str:
            user = user_map.get(uid)
            ingame_name = (user.ingame_name if user else None) or "Unknown"
            return f"<@{uid}> - {ingame_name}"

        has_ticket_lines = (
            "\n".join(f"- có vé: {_fmt_player(uid)}" for uid, _ in has_ticket_group)
            or "- có vé: _(không có ai)_"
        )
        no_ticket_lines = (
            "\n".join(f"- không có vé: {_fmt_player(uid)}" for uid, _ in no_ticket_group)
            or "- không có vé: _(không có ai)_"
        )

        await _announce(
            f"📋 **Trận #{match.id}** – Danh sách người chơi check-in:\n"
            f"{has_ticket_lines}\n"
            f"{no_ticket_lines}"
        )

    # ── Step 3: ELO-based eligibility filter ──────────────────────────────────
    cannot_join_group: list[tuple[int, int]] = []
    if has_ticket_group:
        lowest_ticket_elo = has_ticket_group[-1][1]
        eligible_no_ticket: list[tuple[int, int]] = []
        for i, (uid, elo) in enumerate(no_ticket_group):
            if elo > lowest_ticket_elo:
                cannot_join_group.append((uid, elo))
            else:
                # List is sorted desc – this player and everyone below are eligible
                eligible_no_ticket = no_ticket_group[i:]
                break
        no_ticket_group = eligible_no_ticket

    if cannot_join_group and announce_channel:
        names = ", ".join(f"<@{uid}>" for uid, _ in cannot_join_group)
        await _announce(
            f"⚠️ **Trận #{match.id}** – Những người chơi sau không đủ điều kiện "
            f"tham gia (ELO cao hơn ELO thấp nhất của nhóm có vé nhưng bản thân lại không có vé): "
            f"{names}"
        )

    # ── Step 4: Build list of lobby specs ─────────────────────────────────────
    # Each spec: (tier, lobby_number, [user_ids], ai_count)
    lobby_specs: list[tuple[str, int, list[int], int]] = []

    def _build_specs(player_ids: list[int], tiers: list[str]) -> None:
        """Slice player_ids into tiered lobbies of MAX_LOBBY_SIZE, appending to lobby_specs."""
        remaining = player_ids.copy()
        tier_counters: dict[str, int] = {}

        for tier in tiers:
            if not remaining:
                break
            chunk = remaining[:MAX_LOBBY_SIZE]
            remaining = remaining[MAX_LOBBY_SIZE:]

            tier_counters[tier] = tier_counters.get(tier, 0) + 1
            lobby_num = tier_counters[tier]
            n = len(chunk)

            if n < MIN_LOBBY_SIZE:
                if announce_channel:
                    # We schedule the coroutine; handled after the loop via gathered messages
                    _pending_cancels.append(
                        _announce(
                            f"❌ **Trận #{match.id}** – "
                            f"Lobby {tier} #{lobby_num} bị hủy vì không đủ số người "
                            f"tối thiểu là {MIN_LOBBY_SIZE} (chỉ có {n} người)."
                        )
                    )
                continue

            ai_count = MAX_LOBBY_SIZE - n  # 0, 1, or 2
            lobby_specs.append((tier, lobby_num, chunk, ai_count))

        # Handle overflow for diamond tier (every extra group beyond first two tiers)
        diamond_num = tier_counters.get(TIER_DIAMOND, 0)
        while remaining:
            diamond_num += 1
            chunk = remaining[:MAX_LOBBY_SIZE]
            remaining = remaining[MAX_LOBBY_SIZE:]
            n = len(chunk)

            if n < MIN_LOBBY_SIZE:
                if announce_channel:
                    _pending_cancels.append(
                        _announce(
                            f"❌ **Trận #{match.id}** – "
                            f"Lobby {TIER_DIAMOND} #{diamond_num} bị hủy vì không đủ "
                            f"số người tối thiểu là {MIN_LOBBY_SIZE} (chỉ có {n} người)."
                        )
                    )
                continue

            ai_count = MAX_LOBBY_SIZE - n
            lobby_specs.append((TIER_DIAMOND, diamond_num, chunk, ai_count))

    _pending_cancels: list = []

    # --- has_ticket_group ---
    ticket_player_ids = [uid for uid, _ in has_ticket_group]
    if len(ticket_player_ids) < MIN_LOBBY_SIZE and ticket_player_ids:
        if announce_channel:
            await _announce(
                f"❌ **Trận #{match.id}** – Nhóm có vé bị hủy vì không đủ số người "
                f"tối thiểu là {MIN_LOBBY_SIZE} (chỉ có {len(ticket_player_ids)} người)."
            )
    elif ticket_player_ids:
        _build_specs(
            ticket_player_ids,
            [TIER_LEGENDARY, TIER_CONQUEST],  # first two tiers; diamond handled by overflow
        )

    # --- no_ticket_group ---
    recruit_player_ids = [uid for uid, _ in no_ticket_group]
    if len(recruit_player_ids) < MIN_LOBBY_SIZE and recruit_player_ids:
        if announce_channel:
            await _announce(
                f"❌ **Trận #{match.id}** – Nhóm không vé bị hủy vì không đủ số người "
                f"tối thiểu là {MIN_LOBBY_SIZE} (chỉ có {len(recruit_player_ids)} người)."
            )
    elif recruit_player_ids:
        remaining = recruit_player_ids.copy()
        lobby_num = 0
        while remaining:
            lobby_num += 1
            chunk = remaining[:MAX_LOBBY_SIZE]
            remaining = remaining[MAX_LOBBY_SIZE:]
            n = len(chunk)

            if n < MIN_LOBBY_SIZE:
                if announce_channel:
                    _pending_cancels.append(
                        _announce(
                            f"❌ **Trận #{match.id}** – "
                            f"Lobby {TIER_RECRUIT} #{lobby_num} bị hủy vì không đủ số người "
                            f"tối thiểu là {MIN_LOBBY_SIZE} (chỉ có {n} người)."
                        )
                    )
                continue

            ai_count = MAX_LOBBY_SIZE - n
            lobby_specs.append((TIER_RECRUIT, lobby_num, chunk, ai_count))

    # Send all pending cancel announcements
    for coro in _pending_cancels:
        try:
            await coro
        except Exception as exc:
            log.exception(
                "Failed to send cancel announcement for match #%s: %s", match.id, exc
            )

    if not lobby_specs:
        if announce_channel:
            await _announce(
                f"⚠️ **Trận #{match.id}** – Không có lobby nào được tạo."
            )
        with db_session_factory() as session:
            db_match = session.get(Match, match.id)
            if db_match is not None:
                db_match.status = "cancelled"
                if db_match.end_time is None:
                    db_match.end_time = now_vn()
                session.commit()
        return

    # ── Step 5: Build player name map ─────────────────────────────────────────
    all_player_ids = list({uid for _, _, players, _ in lobby_specs for uid in players})
    with db_session_factory() as session:
        users = session.query(User).filter(User.id.in_(all_player_ids)).all()
    p_map = {u.id: (u.ingame_name or "Unknown") for u in users}

    # ── Step 6: Guild / role / category objects ────────────────────────────────
    guild = bot.guilds[0] if bot.guilds else None
    judge_role = guild.get_role(JUDGE_ROLE_ID) if (guild and JUDGE_ROLE_ID) else None
    category_obj = (
        guild.get_channel(LOBBY_CATEGORY_ID)
        if (guild and LOBBY_CATEGORY_ID)
        else None
    )
    category = category_obj if isinstance(category_obj, discord.CategoryChannel) else None
    if guild and LOBBY_CATEGORY_ID and category is None:
        log.warning(
            "Configured LOBBY_CATEGORY_ID=%s is missing or not a category channel",
            LOBBY_CATEGORY_ID,
        )

    # ── Step 7: Persist lobbies, create channels, post embeds ─────────────────
    if announce_channel:
        await _announce(
            f"✅ **Trận #{match.id}** – Bắt đầu chia {len(lobby_specs)} lobby…"
        )

    # Pre-build emoji map once so we can resolve :name: → <:name:id> per lobby
    guild_emoji_map: dict[str, str] = _build_emoji_map(guild) if guild else {}

    for tier, lobby_num, player_ids, ai_count in lobby_specs:
        # Build civ keys: real players + AI slots
        ai_keys = [f"AI_{i + 1}" for i in range(ai_count)]
        all_civ_keys = [str(uid) for uid in player_ids] + ai_keys

        try:
            civs = assign_civs(all_civ_keys, match.count_fight)
        except ValueError as exc:
            if announce_channel:
                await _announce(
                    f"⚠️ Không thể chia civ cho Lobby {tier} #{lobby_num}: {exc}"
                )
            civs = {}

        # Resolve any :name: emoji strings to <:name:id> so they render in Discord
        if guild_emoji_map:
            civs = {
                key: [_resolve_emoji_str(c, guild_emoji_map) for c in civ_list]
                for key, civ_list in civs.items()
            }

        # Persist lobby to DB
        with db_session_factory() as session:
            db_match = session.get(Match, match.id)
            if db_match is None:
                continue

            lobby = Lobby(
                match_id=match.id,
                tier=tier,
                lobby_number=lobby_num,
                users_list=player_ids,
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

        # Create voice + text channels
        voice_ids: list[int] = []
        text_ids: list[int] = []
        if guild:
            try:
                with db_session_factory() as session:
                    db_lobby = session.get(Lobby, lobby_id)
                    db_match = session.get(Match, match.id)
                    if db_lobby and db_match:
                        voice_ids, text_ids = await create_lobby_channels(
                            guild, db_lobby, db_match, p_map, category, judge_role
                        )
                        db_lobby.voice_channel_ids = voice_ids
                        db_lobby.text_channel_ids = text_ids
                        session.commit()
            except Exception as exc:
                log.exception("Channel creation error (lobby #%s): %s", lobby_id, exc)

        # Re-load lobby data for embeds
        with db_session_factory() as session:
            db_lobby = session.get(Lobby, lobby_id)
            db_match = session.get(Match, match.id)
            if db_lobby is None or db_match is None:
                continue

            # Take snapshots while session is open
            lobby_snap = _LobbySnapshot(db_lobby)
            match_snap = _MatchSnapshot(db_match)

        # Send display embed to announce channel (with player @mentions above the embed)
        if announce_channel:
            mentions = " ".join(f"<@{uid}>" for uid in (lobby_snap.users_list or []))
            display_file = await build_lobby_display_image_file(lobby_snap, match_snap, p_map)
            display_message_ids: list[int] = []
            if display_file is not None:
                display_embed = build_lobby_display_embed(lobby_snap, match_snap)
                display_msg = await _send_lobby_display(content=mentions or None, embed=display_embed, file=display_file)
                if display_msg is not None:
                    display_message_ids.append(display_msg.id)
            else:
                display_messages = build_lobby_display_messages(lobby_snap, match_snap, p_map)
                if display_messages:
                    fallback_embed = discord.Embed(
                        title=f"{TIER_EMOJI.get(lobby_snap.tier or '', '🎮')} Chia Lobby - Trận #{match_snap.id}",
                        description=display_messages[0],
                        color=discord.Color.blue(),
                    )
                    display_msg = await _send_lobby_display(content=mentions or None, embed=fallback_embed)
                    if display_msg is not None:
                        display_message_ids.append(display_msg.id)
                    for extra_message in display_messages[1:]:
                        extra_msg = await _send_lobby_display(content=extra_message)
                        if extra_msg is not None:
                            display_message_ids.append(extra_msg.id)

            if display_message_ids:
                with db_session_factory() as session:
                    db_lobby = session.get(Lobby, lobby_id)
                    if db_lobby is not None:
                        ids = list(db_lobby.display_message_ids or [])
                        for message_id in display_message_ids:
                            if message_id not in ids:
                                ids.append(message_id)
                        db_lobby.display_message_ids = ids
                        session.commit()

        # Result-entry message is posted later by scheduler at match start time.


# ── Lightweight snapshot dataclasses (avoid detached-instance issues) ──────────

class _LobbySnapshot:
    """Plain-object copy of the fields we need from a Lobby ORM row."""

    __slots__ = (
        "id", "match_id", "tier", "lobby_number",
        "users_list", "ai_count", "civs", "scores",
        "status", "voice_channel_ids", "text_channel_ids", "result_message_id",
    )

    def __init__(self, lobby) -> None:
        for attr in self.__slots__:
            setattr(self, attr, getattr(lobby, attr, None))


class _MatchSnapshot:
    """Plain-object copy of the fields we need from a Match ORM row."""

    __slots__ = (
        "id", "count_fight", "name_maps", "time_start",
        "time_reach_checkin", "time_reach_divide_lobby",
    )

    def __init__(self, match) -> None:
        for attr in self.__slots__:
            setattr(self, attr, getattr(match, attr, None))
