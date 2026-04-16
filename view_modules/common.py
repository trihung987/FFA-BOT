from __future__ import annotations

import logging

import discord

from config import MIN_PLAYERS_REQUIRED as _MIN_PLAYERS_REQUIRED
from helpers import format_vn_time, parse_duration, safe_send_interaction

log = logging.getLogger(__name__)


async def _safe_edit(interaction: discord.Interaction, context: str, **kwargs) -> None:
    """Edit the original interaction message, logging timeout/HTTP errors."""
    try:
        await interaction.response.edit_message(**kwargs)
    except discord.NotFound as exc:
        if exc.code == 10062:
            log.warning("Interaction expired (%s, user=%s)", context, interaction.user.id)
        else:
            log.error("NotFound editing message (%s, user=%s): %s", context, interaction.user.id, exc)
    except discord.HTTPException as exc:
        log.error("HTTP error editing message (%s, user=%s): %s", context, interaction.user.id, exc)


async def _safe_send(interaction: discord.Interaction, context: str, *args, **kwargs) -> None:
    """Compatibility wrapper around the shared interaction sender."""
    await safe_send_interaction(interaction, context, *args, **kwargs)


async def _safe_edit_original_response(interaction: discord.Interaction, context: str, *args, **kwargs) -> None:
    """Edit the original interaction response, logging timeout/HTTP errors."""
    try:
        await interaction.edit_original_response(*args, **kwargs)
    except discord.NotFound as exc:
        if exc.code == 10062:
            log.warning("Interaction expired (%s, user=%s)", context, interaction.user.id)
        else:
            log.error("NotFound editing original response (%s, user=%s): %s", context, interaction.user.id, exc)
    except discord.HTTPException as exc:
        log.error("HTTP error editing original response (%s, user=%s): %s", context, interaction.user.id, exc)


async def _safe_defer(
    interaction: discord.Interaction,
    context: str,
    *,
    ephemeral: bool = True,
    thinking: bool = True,
) -> bool:
    """Defer an interaction response and return whether it succeeded."""
    if interaction.response.is_done():
        return True

    try:
        await interaction.response.defer(ephemeral=ephemeral, thinking=thinking)
        return True
    except discord.NotFound as exc:
        if exc.code == 10062:
            log.warning("Interaction expired (%s, user=%s)", context, interaction.user.id)
        else:
            log.error("NotFound deferring response (%s, user=%s): %s", context, interaction.user.id, exc)
    except discord.HTTPException as exc:
        log.error("HTTP error deferring response (%s, user=%s): %s", context, interaction.user.id, exc)
    return False


def _set_view_items_disabled(view: discord.ui.View, disabled: bool) -> None:
    """Enable/disable all view components that support the disabled attribute."""
    for item in view.children:
        if hasattr(item, "disabled"):
            item.disabled = disabled


async def _safe_message_edit(message: discord.Message, context: str, user_id: int, **kwargs) -> None:
    """Edit a message directly, logging NotFound/HTTP errors."""
    try:
        await message.edit(**kwargs)
    except discord.NotFound as exc:
        log.warning("Message not found while editing (%s, user=%s): %s", context, user_id, exc)
    except discord.HTTPException as exc:
        log.error("HTTP error editing message (%s, user=%s): %s", context, user_id, exc)


def _load_player_map(session, user_ids: list[int]) -> dict[int, str]:
    """Return a dict mapping Discord user ID -> in-game name (fallback: 'Unknown')."""
    from entity import User

    if not user_ids:
        return {}
    users = session.query(User).filter(User.id.in_(user_ids)).all()
    return {u.id: (u.ingame_name or "Unknown") for u in users}


def _load_player_ticket_map(session, user_ids: list[int]) -> dict[int, int]:
    """Return a dict mapping Discord user ID -> current ticket count."""
    from entity import User

    if not user_ids:
        return {}
    users = session.query(User).filter(User.id.in_(user_ids)).all()
    return {u.id: int(u.ticket or 0) for u in users}


def _ticket_status(ticket_map: dict[int, int], uid: int) -> str:
    return "có vé" if ticket_map.get(uid, 0) > 0 else "không có vé"


def _count_ticket_groups(user_ids: list[int], ticket_map: dict[int, int]) -> tuple[int, int]:
    with_ticket = sum(1 for uid in user_ids if ticket_map.get(uid, 0) > 0)
    without_ticket = len(user_ids) - with_ticket
    return with_ticket, without_ticket


def build_registered_mentions(user_ids: list[int] | None) -> str:
    """Build a de-duplicated mention string for registered players."""
    if not user_ids:
        return ""

    seen: set[int] = set()
    ordered_ids: list[int] = []
    for uid in user_ids:
        if not isinstance(uid, int):
            continue
        if uid in seen:
            continue
        seen.add(uid)
        ordered_ids.append(uid)
    return " ".join(f"<@{uid}>" for uid in ordered_ids)


def build_registration_embed(
    match,
    p_map: dict[int, str],
    ticket_map: dict[int, int] | None = None,
    *,
    checkin_started: bool = False,
    cancelled: bool = False,
) -> discord.Embed:
    """Build (or rebuild) the registration embed for a match."""
    time_start = match.time_start

    try:
        divide_open_dt = time_start - parse_duration(match.time_reach_divide_lobby)
        divide_display = format_vn_time(divide_open_dt)
    except (ValueError, AttributeError):
        divide_open_dt = None
        divide_display = "N/A"

    try:
        checkin_open_dt = time_start - parse_duration(match.time_reach_checkin)
        checkin_end_dt = divide_open_dt if divide_open_dt is not None else time_start
        checkin_display = f"{format_vn_time(checkin_open_dt)} -> {format_vn_time(checkin_end_dt)}"
    except ValueError:
        checkin_display = "N/A"

    registered = match.register_users_id or []
    map_names = match.name_maps or []
    ticket_map = ticket_map or {}
    with_ticket_count, without_ticket_count = _count_ticket_groups(registered, ticket_map)

    reg_list_str = (
        "\n".join(
            f"<@{uid}> - {p_map.get(uid, 'Unknown')} - {_ticket_status(ticket_map, uid)}"
            for uid in registered
        )
        or "_(chưa có ai)_"
    )

    body = (
        f"🆔 **ID trận:** #{match.id}\n"
        f"🎯 **Số trận:** {match.count_fight}\n"
        f"⏰ **Check-in:** {checkin_display}\n"
        f"🔀 **Chia lobby:** {divide_display}\n"
        f"📅 **Giờ bắt đầu:** {format_vn_time(time_start)}\n"
        f"🗺️ **Maps:** {', '.join(map_names)}\n\n"
        f"👥 **Đã đăng ký: {len(registered)} người**\n"
        f"🎫 **Có vé:** {with_ticket_count} | **Không có vé:** {without_ticket_count}\n"
        f"{reg_list_str}"
    )

    if cancelled:
        body += (
            f"\n\n❌ **Trận đã bị hủy vì không đủ người đăng ký "
            f"({len(registered)}/{_MIN_PLAYERS_REQUIRED} người tối thiểu).**"
        )
    elif checkin_started:
        body += "\n\n⏰ **Đã đến giờ check-in! Đăng ký đã đóng.**"
    else:
        body += "\n\nNhấn **Tham gia** để đăng ký hoặc **Hủy đăng ký** để rút tên."

    return discord.Embed(
        title=f"🎮 Đăng Ký Tham Gia FFA #{match.id}",
        description=body,
        color=discord.Color.red() if cancelled else discord.Color.blue(),
    )


def build_checkin_embed(
    match,
    p_map: dict[int, str],
    ticket_map: dict[int, int] | None = None,
    *,
    ended: bool = False,
    cancelled: bool = False,
) -> discord.Embed:
    """Build (or rebuild) the check-in embed for a match."""
    time_start = match.time_start
    registered = match.register_users_id or []
    checked_in = match.checkin_users_id or []
    ticket_map = ticket_map or {}
    with_ticket_count, without_ticket_count = _count_ticket_groups(checked_in, ticket_map)

    try:
        checkin_open_dt = time_start - parse_duration(match.time_reach_checkin)
        divide_dt = time_start - parse_duration(match.time_reach_divide_lobby)
        checkin_window = f"{format_vn_time(checkin_open_dt)} -> {format_vn_time(divide_dt)}"
    except (ValueError, AttributeError):
        checkin_window = "N/A"

    checkin_list_str = (
        "\n".join(
            f"- {p_map.get(u, 'Unknown')} - {_ticket_status(ticket_map, u)} ✅"
            for u in checked_in
        )
        or "_(chưa có ai)_"
    )

    body = (
        f"🆔 **ID trận:** #{match.id}\n"
        f"⏰ **Thời gian check-in:** {checkin_window}\n"
        f"👥 **Đã check-in:** {len(checked_in)}/{len(registered)}\n\n"
        f"🎫 **Có vé:** {with_ticket_count} | **Không có vé:** {without_ticket_count}\n"
        f"✅ **Danh sách check-in:**\n{checkin_list_str}"
    )

    if cancelled:
        body += (
            f"\n\n❌ **Trận đã bị hủy vì không đủ người check-in tối thiểu "
            f"({len(checked_in)}/{_MIN_PLAYERS_REQUIRED} người tối thiểu).**"
        )
    elif ended:
        body += "\n\n🔒 **Check-in đã kết thúc. Đang tiến hành chia lobby...**"
    else:
        body += "\n\nNhấn **Sẵn sàng ✅** để xác nhận tham gia."

    embed = discord.Embed(
        title="📋 Check-in FFA Trận",
        description=body,
        color=discord.Color.red() if cancelled else (discord.Color.dark_gray() if ended else discord.Color.green()),
    )
    embed.set_footer(text=f"ID trận: {match.id}")
    return embed
