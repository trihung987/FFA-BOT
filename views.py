"""
Discord UI components (Views and Modals) for the FFA bot.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import discord

from config import SHOWMATCH_ROLE_ID as _SHOWMATCH_ROLE_ID, MIN_PLAYERS_REQUIRED as _MIN_PLAYERS_REQUIRED
from helpers import format_vn_time, now_vn, parse_duration

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


# ── Interaction response helpers ───────────────────────────────────────────────


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
    """Send an ephemeral interaction response, logging timeout/HTTP errors."""
    try:
        await interaction.response.send_message(*args, **kwargs)
    except discord.NotFound as exc:
        if exc.code == 10062:
            log.warning("Interaction expired (%s, user=%s)", context, interaction.user.id)
        else:
            log.error("NotFound sending response (%s, user=%s): %s", context, interaction.user.id, exc)
    except discord.HTTPException as exc:
        log.error("HTTP error sending response (%s, user=%s): %s", context, interaction.user.id, exc)


# ── Shared helpers ─────────────────────────────────────────────────────────────


def _load_player_map(session, user_ids: list[int]) -> dict[int, str]:
    """Return a dict mapping Discord user ID → in-game name (fallback: 'Unknown')."""
    from entity import User

    if not user_ids:
        return {}
    users = session.query(User).filter(User.id.in_(user_ids)).all()
    return {u.id: (u.ingame_name or "Unknown") for u in users}


def build_registration_embed(match, p_map: dict[int, str], *, checkin_started: bool = False, cancelled: bool = False) -> discord.Embed:
    """Build (or rebuild) the registration embed for *match*.

    Parameters
    ----------
    match:
        A ``Match`` ORM instance (must still be attached to a session, or all
        needed attributes already loaded).
    p_map:
        Dict mapping Discord user ID (int) → in-game name (str).
    checkin_started:
        When *True*, append a notice that check-in is now open and registration
        is closed.
    cancelled:
        When *True*, append a cancellation notice and use a red embed color.
    """
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
        checkin_display = f"{format_vn_time(checkin_open_dt)} → {format_vn_time(checkin_end_dt)}"
    except ValueError:
        checkin_display = "N/A"

    registered = match.register_users_id or []
    map_names = match.name_maps or []

    reg_list_str = (
        "\n".join(f"<@{uid}> - {p_map.get(uid, 'Unknown')}" for uid in registered)
        or "_(chưa có ai)_"
    )

    body = (
        f"🆔 **Match ID:** #{match.id}\n"
        f"🎯 **Số trận:** {match.count_fight}\n"
        f"⏰ **Check-in:** {checkin_display}\n"
        f"🔀 **Chia lobby:** {divide_display}\n"
        f"📅 **Giờ bắt đầu:** {format_vn_time(time_start)}\n"
        f"🗺️ **Maps:** {', '.join(map_names)}\n\n"
        f"👥 **Đã đăng ký: {len(registered)} người**\n"
        f"{reg_list_str}"
    )

    if cancelled:
        body += (
            f"\n\n❌ **Match đã bị hủy vì không đủ người đăng ký "
            f"({len(registered)}/{_MIN_PLAYERS_REQUIRED} người tối thiểu).**"
        )
    elif checkin_started:
        body += "\n\n⏰ **Đã đến giờ check-in! Đăng ký đã đóng.**"
    else:
        body += "\n\nNhấn **Tham gia** để đăng ký hoặc **Hủy đăng ký** để rút tên."

    embed = discord.Embed(
        title=f"🎮 Đăng Ký Tham Gia FFA #{match.id}",
        description=body,
        color=discord.Color.red() if cancelled else discord.Color.blue(),
    )
    return embed


def build_checkin_embed(match, p_map: dict[int, str], *, ended: bool = False, cancelled: bool = False) -> discord.Embed:
    """Build (or rebuild) the check-in embed for *match*.

    Parameters
    ----------
    match:
        A ``Match`` ORM instance.
    p_map:
        Dict mapping Discord user ID (int) → in-game name (str).
    ended:
        When *True*, append a notice that check-in is closed and lobby
        division is starting, and use a grey color.
    cancelled:
        When *True*, append a cancellation notice and use a red embed color.
    """
    time_start = match.time_start
    registered = match.register_users_id or []
    checked_in = match.checkin_users_id or []

    try:
        checkin_open_dt = time_start - parse_duration(match.time_reach_checkin)
        divide_dt = time_start - parse_duration(match.time_reach_divide_lobby)
        checkin_window = f"{format_vn_time(checkin_open_dt)} → {format_vn_time(divide_dt)}"
    except (ValueError, AttributeError):
        checkin_window = "N/A"

    checkin_list_str = (
        "\n".join(f"- {p_map.get(u, 'Unknown')} ✅" for u in checked_in)
        or "_(chưa có ai)_"
    )

    body = (
        f"🆔 **Match ID:** #{match.id}\n"
        f"⏰ **Thời gian check-in:** {checkin_window}\n"
        f"👥 **Đã check-in:** {len(checked_in)}/{len(registered)}\n\n"
        f"✅ **Danh sách check-in:**\n{checkin_list_str}"
    )

    if cancelled:
        body += (
            f"\n\n❌ **Match đã bị hủy vì không đủ người check-in tối thiểu "
            f"({len(checked_in)}/{_MIN_PLAYERS_REQUIRED} người tối thiểu).**"
        )
    elif ended:
        body += "\n\n🔒 **Check-in đã kết thúc. Đang tiến hành chia lobby...**"
    else:
        body += "\n\nNhấn **Sẵn sàng ✅** để xác nhận tham gia."

    embed = discord.Embed(
        title="📋 Check-in FFA Match",
        description=body,
        color=discord.Color.red() if cancelled else (discord.Color.dark_gray() if ended else discord.Color.green()),
    )
    embed.set_footer(text=f"Match ID: {match.id}")
    return embed


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
        from entity import Match

        map_names = [field.value for field in self._map_inputs]

        # Parse time_start string into a datetime object (Vietnam local time)
        try:
            time_start_dt = datetime.strptime(self.time_start, "%Y-%m-%d %H:%M")
        except ValueError:
            await _safe_send(
                interaction, "MapNamesModal.on_submit",
                "❌ Định dạng thời gian không hợp lệ. Vui lòng dùng: YYYY-MM-DD HH:MM",
                ephemeral=True,
            )
            return

        # Validate duration fields before creating the match
        try:
            checkin_delta = parse_duration(self.time_reach_checkin)
        except ValueError:
            await _safe_send(
                interaction, "MapNamesModal.on_submit",
                f"❌ Định dạng thời gian check-in không hợp lệ: {self.time_reach_checkin!r}. "
                "Vui lòng dùng: 1h hoặc 30p",
                ephemeral=True,
            )
            return

        try:
            divide_delta = parse_duration(self.time_reach_divide_lobby)
        except ValueError:
            await _safe_send(
                interaction, "MapNamesModal.on_submit",
                f"❌ Định dạng thời gian chia lobby không hợp lệ: {self.time_reach_divide_lobby!r}. "
                "Vui lòng dùng: 1h hoặc 30p",
                ephemeral=True,
            )
            return

        if checkin_delta <= divide_delta:
            await _safe_send(
                interaction, "MapNamesModal.on_submit",
                "❌ Thời gian mở check-in phải lớn hơn thời gian chia lobby. "
                f"({self.time_reach_checkin} phải trước {self.time_reach_divide_lobby})",
                ephemeral=True,
            )
            return

        # Save the match to the database and build the initial embed (no registrations yet)
        try:
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
                embed = build_registration_embed(match, {})
        except Exception as exc:
            log.exception(
                "DB error creating match in MapNamesModal.on_submit (user=%s)",
                interaction.user.id,
            )
            await _safe_send(
                interaction, "MapNamesModal.on_submit",
                "❌ Đã xảy ra lỗi nội bộ khi tạo match.", ephemeral=True,
            )
            return

        view = RegistrationView(match_id=match_id, db_session_factory=self.db_session_factory)
        role_mention = f"<@&{_SHOWMATCH_ROLE_ID}>" if _SHOWMATCH_ROLE_ID else None
        try:
            reg_msg = await self.register_channel.send(content=role_mention, embed=embed, view=view)
        except discord.HTTPException as exc:
            log.exception(
                "Failed to send registration message for match #%s (user=%s)",
                match_id, interaction.user.id,
            )
            await _safe_send(
                interaction, "MapNamesModal.on_submit",
                "❌ Không thể gửi thông báo đăng ký. Vui lòng thử lại.", ephemeral=True,
            )
            return

        # Persist the registration message ID so the scheduler can disable it later
        try:
            with self.db_session_factory() as session:
                db_match = session.get(Match, match_id)
                if db_match is not None:
                    db_match.register_message_id = reg_msg.id
                    session.commit()
        except Exception as exc:
            log.exception(
                "DB error saving register_message_id for match #%s", match_id
            )

        await _safe_send(
            interaction, "MapNamesModal.on_submit",
            f"✅ Đã mở đăng ký cho match #{match_id}!", ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception(
            "Unhandled error in MapNamesModal (user=%s)", interaction.user.id
        )
        if not interaction.response.is_done():
            await _safe_send(
                interaction, "MapNamesModal.on_error",
                "❌ Đã xảy ra lỗi không mong muốn.", ephemeral=True,
            )


# ── View: registration embed with Join / Cancel buttons ───────────────────────


def build_disabled_registration_view() -> discord.ui.View:
    """Return a view with the registration buttons (Join/Cancel) disabled."""
    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="Tham gia", style=discord.ButtonStyle.success, emoji="✅", disabled=True,
    ))
    view.add_item(discord.ui.Button(
        label="Hủy đăng ký", style=discord.ButtonStyle.danger, emoji="❌", disabled=True,
    ))
    return view


def build_disabled_checkin_view() -> discord.ui.View:
    """Return a view with the check-in button (Sẵn sàng ✅) disabled."""
    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="Sẵn sàng ✅", style=discord.ButtonStyle.primary, disabled=True,
    ))
    return view


class RegistrationView(discord.ui.View):
    """
    Persistent view attached to the registration embed.
    Provides **Tham gia** (Join) and **Hủy đăng ký** (Cancel) buttons.
    The embed is rebuilt in-place on every action so the player list stays current.
    """

    def __init__(self, match_id: int, db_session_factory) -> None:
        # timeout=None makes the view persist until the bot restarts
        super().__init__(timeout=None)
        self.match_id = match_id
        self.db_session_factory = db_session_factory

    @discord.ui.button(label="Tham gia", style=discord.ButtonStyle.success, emoji="✅")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from entity import Match, User

        user_id = interaction.user.id

        try:
            with self.db_session_factory() as session:
                # Ensure the player has set up their profile first
                player = session.get(User, user_id)
                if player is None:
                    await _safe_send(
                        interaction, f"RegistrationView.join match={self.match_id}",
                        "❌ Bạn chưa có hồ sơ FFA. Vui lòng dùng `/set_ingame_name` trước khi đăng ký.",
                        ephemeral=True,
                    )
                    return

                match: Match | None = session.get(Match, self.match_id)
                if match is None:
                    await _safe_send(
                        interaction, f"RegistrationView.join match={self.match_id}",
                        "❌ Match không tồn tại.", ephemeral=True,
                    )
                    return

                registered: list = (match.register_users_id or []).copy()
                if user_id in registered:
                    await _safe_send(
                        interaction, f"RegistrationView.join match={self.match_id}",
                        "⚠️ Bạn đã đăng ký rồi!", ephemeral=True,
                    )
                    return

                registered.append(user_id)
                match.register_users_id = registered
                session.commit()
                session.refresh(match)

                p_map = _load_player_map(session, registered)
                new_embed = build_registration_embed(match, p_map)
        except Exception as exc:
            log.exception(
                "Error in RegistrationView.join (match=%s, user=%s)",
                self.match_id, user_id,
            )
            if not interaction.response.is_done():
                await _safe_send(
                    interaction, f"RegistrationView.join match={self.match_id}",
                    "❌ Đã xảy ra lỗi nội bộ.", ephemeral=True,
                )
            return

        # Edit the embed in-place; the updated list is the confirmation
        await _safe_edit(
            interaction, f"RegistrationView.join match={self.match_id}",
            embed=new_embed, view=self,
        )

    @discord.ui.button(label="Hủy đăng ký", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from entity import Match

        user_id = interaction.user.id

        try:
            with self.db_session_factory() as session:
                match: Match | None = session.get(Match, self.match_id)
                if match is None:
                    await _safe_send(
                        interaction, f"RegistrationView.cancel match={self.match_id}",
                        "❌ Match không tồn tại.", ephemeral=True,
                    )
                    return

                registered: list = (match.register_users_id or []).copy()
                if user_id not in registered:
                    await _safe_send(
                        interaction, f"RegistrationView.cancel match={self.match_id}",
                        "⚠️ Bạn chưa đăng ký!", ephemeral=True,
                    )
                    return

                registered.remove(user_id)
                match.register_users_id = registered
                session.commit()
                session.refresh(match)

                p_map = _load_player_map(session, registered)
                new_embed = build_registration_embed(match, p_map)
        except Exception as exc:
            log.exception(
                "Error in RegistrationView.cancel (match=%s, user=%s)",
                self.match_id, user_id,
            )
            if not interaction.response.is_done():
                await _safe_send(
                    interaction, f"RegistrationView.cancel match={self.match_id}",
                    "❌ Đã xảy ra lỗi nội bộ.", ephemeral=True,
                )
            return

        await _safe_edit(
            interaction, f"RegistrationView.cancel match={self.match_id}",
            embed=new_embed, view=self,
        )


# ── View: check-in embed with Ready button ────────────────────────────────────


class CheckInView(discord.ui.View):
    """
    Persistent view attached to the check-in embed.
    Provides a **Sẵn sàng ✅** button for registered players to confirm attendance.
    The embed is rebuilt in-place on every check-in so the list stays current.
    """

    def __init__(self, match_id: int, db_session_factory) -> None:
        super().__init__(timeout=None)
        self.match_id = match_id
        self.db_session_factory = db_session_factory

    @discord.ui.button(label="Sẵn sàng ✅", style=discord.ButtonStyle.primary)
    async def ready(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from entity import Match, User

        user_id = interaction.user.id

        try:
            with self.db_session_factory() as session:
                # Ensure the player has set up their profile first
                player = session.get(User, user_id)
                if player is None:
                    await _safe_send(
                        interaction, f"CheckInView.ready match={self.match_id}",
                        "❌ Bạn chưa có hồ sơ FFA. Vui lòng dùng `/set_ingame_name` trước khi check-in.",
                        ephemeral=True,
                    )
                    return

                match: Match | None = session.get(Match, self.match_id)
                if match is None:
                    await _safe_send(
                        interaction, f"CheckInView.ready match={self.match_id}",
                        "❌ Match không tồn tại.", ephemeral=True,
                    )
                    return

                registered: list = match.register_users_id or []
                if user_id not in registered:
                    await _safe_send(
                        interaction, f"CheckInView.ready match={self.match_id}",
                        "⚠️ Bạn chưa đăng ký tham gia match này!", ephemeral=True,
                    )
                    return

                # Validate that the current time is within the check-in window
                try:
                    now = now_vn()
                    checkin_open = match.time_start - parse_duration(match.time_reach_checkin)
                    divide_time = match.time_start - parse_duration(match.time_reach_divide_lobby)
                    if now < checkin_open:
                        await _safe_send(
                            interaction, f"CheckInView.ready match={self.match_id}",
                            f"⏳ Chưa đến giờ check-in! Giờ mở check-in: {format_vn_time(checkin_open)}",
                            ephemeral=True,
                        )
                        return
                    if now >= divide_time:
                        await _safe_send(
                            interaction, f"CheckInView.ready match={self.match_id}",
                            f"⌛ Đã hết thời gian check-in! Check-in kết thúc lúc: {format_vn_time(divide_time)}",
                            ephemeral=True,
                        )
                        return
                except ValueError:
                    log.warning(
                        "CheckInView.ready: could not parse check-in window for match #%s – allowing check-in",
                        self.match_id,
                    )

                checked_in: list = (match.checkin_users_id or []).copy()
                if user_id in checked_in:
                    await _safe_send(
                        interaction, f"CheckInView.ready match={self.match_id}",
                        "⚠️ Bạn đã check-in rồi!", ephemeral=True,
                    )
                    return

                checked_in.append(user_id)
                match.checkin_users_id = checked_in
                session.commit()
                session.refresh(match)

                # Load names for everyone relevant to the embed
                all_user_ids = list(set((match.register_users_id or []) + checked_in))
                p_map = _load_player_map(session, all_user_ids)
                new_embed = build_checkin_embed(match, p_map)
        except Exception as exc:
            log.exception(
                "Error in CheckInView.ready (match=%s, user=%s)",
                self.match_id, user_id,
            )
            if not interaction.response.is_done():
                await _safe_send(
                    interaction, f"CheckInView.ready match={self.match_id}",
                    "❌ Đã xảy ra lỗi nội bộ.", ephemeral=True,
                )
            return

        await _safe_edit(
            interaction, f"CheckInView.ready match={self.match_id}",
            embed=new_embed, view=self,
        )


# ── Result-entry embed builder ────────────────────────────────────────────────


def build_lobby_result_embed(lobby, match, p_map: dict[int, str] | None = None) -> discord.Embed:
    """Build the result-entry embed posted in the result channel (one per lobby).

    ``lobby`` and ``match`` may be plain snapshot objects or ORM instances –
    only attribute access is used.

    Parameters
    ----------
    p_map:
        Optional dict mapping Discord user ID (int) → in-game name (str).
        When provided, score lines display the in-game name instead of a @mention.
    """
    from lobby_division import TIER_EMOJI, _civ_display_name

    tier: str = lobby.tier or ""
    emoji = TIER_EMOJI.get(tier, "🎮")

    status = lobby.status or "active"
    if status == "finished":
        title = f"{emoji} Kết Quả – Lobby {tier} #{lobby.lobby_number}"
    elif status == "cancelled":
        title = f"{emoji} Đã Hủy Kết Quả – Lobby {tier} #{lobby.lobby_number}"
    else:
        title = f"{emoji} Nhập Kết Quả – Lobby {tier} #{lobby.lobby_number}"

    status_labels = {
        "active": "🟢 Đang diễn ra",
        "cancelled": "🔴 Đã hủy",
        "finished": "✅ Đã kết thúc",
    }
    status_str = status_labels.get(status, status)

    map_names: list = match.name_maps or []
    count_fight: int = match.count_fight
    scores: dict = lobby.scores or {}
    civs_dict: dict = lobby.civs or {}

    lines = [
        f"🆔 **Match:** #{match.id}",
        f"📊 **Trạng thái:** {status_str}",
        f"🎯 **Số trận:** {count_fight}",
        "",
        "**📋 Kết quả đã nhập:**",
    ]

    for i in range(1, count_fight + 1):
        fight_key = f"fight_{i}"
        map_name = map_names[i - 1] if i - 1 < len(map_names) else f"map{i}"
        fight_scores: dict = scores.get(fight_key, {})
        if fight_scores:
            score_lines = []
            for uid, score in fight_scores.items():
                if uid.isdigit() and p_map is not None:
                    name = p_map.get(int(uid), f"<@{uid}>")
                elif uid.isdigit():
                    name = f"<@{uid}>"
                else:
                    name = uid  # AI slot
                # Append the civ name for this fight alongside the player name
                user_civs = civs_dict.get(str(uid), [])
                civ_raw = user_civs[i - 1] if i - 1 < len(user_civs) else ""
                civ_str = _civ_display_name(civ_raw) if civ_raw else ""
                display = f"{name} ({civ_str})" if civ_str else name
                score_lines.append(f"  • {display}: **{score}**")
            lines.append(f"⚔️ **Trận {i} ({map_name}):**\n" + "\n".join(score_lines))
        else:
            lines.append(f"⚔️ **Trận {i} ({map_name}):** _(chưa có kết quả)_")

    if status == "finished":
        color = discord.Color.green()
    elif status == "cancelled":
        color = discord.Color.red()
    else:
        color = discord.Color.orange()

    embed = discord.Embed(
        title=title,
        description="\n".join(lines),
        color=color,
    )
    embed.set_footer(text=f"Lobby ID: {lobby.id} | Match #{match.id}")
    return embed


# ── Score input modals ────────────────────────────────────────────────────────

# Discord modals support a maximum of 5 TextInput components.
_MODAL_PAGE_SIZE = 5


class ScoreModal(discord.ui.Modal):
    """Modal for entering scores for one fight in a lobby.

    For lobbies with more than 5 real players a second modal (page 2) is
    chained automatically after this one is submitted.

    Parameters
    ----------
    lobby_id:
        Primary key of the Lobby row.
    fight_idx:
        1-based fight number.
    page_entries:
        List of ``(str_key, display_label)`` for this modal page (max 5).
        ``str_key`` is ``str(user_id)`` or ``"AI_N"``.
    db_session_factory:
        SQLAlchemy session factory.
    overflow_entries:
        Remaining entries for a chained second modal (empty for the last page).
    partial_scores:
        Scores already collected from previous pages (passed along the chain).
    result_message_id:
        The Discord message ID of the result embed to update after saving.
    result_channel_id:
        The Discord channel ID where *result_message_id* lives.
    """

    def __init__(
        self,
        lobby_id: int,
        fight_idx: int,
        page_entries: list[tuple[str, str]],
        db_session_factory,
        overflow_entries: list[tuple[str, str]] | None = None,
        partial_scores: dict[str, str] | None = None,
        result_message_id: int | None = None,
        result_channel_id: int | None = None,
    ) -> None:
        super().__init__(title=f"Nhập điểm Trận {fight_idx}")
        self.lobby_id = lobby_id
        self.fight_idx = fight_idx
        self.db_session_factory = db_session_factory
        self.overflow_entries = overflow_entries or []
        self.partial_scores: dict[str, str] = partial_scores or {}
        self.result_message_id = result_message_id
        self.result_channel_id = result_channel_id

        self._inputs: list[tuple[str, discord.ui.TextInput]] = []
        for key, label in page_entries:
            inp = discord.ui.TextInput(
                label=label[:44] + "…" if len(label) > 45 else label,
                placeholder="Nhập điểm số",
                required=True,
                max_length=10,
            )
            self._inputs.append((key, inp))
            self.add_item(inp)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from entity import Lobby, Match

        new_scores = {key: inp.value for key, inp in self._inputs}

        if self.overflow_entries:
            # Chain to page 2
            modal2 = ScoreModal(
                lobby_id=self.lobby_id,
                fight_idx=self.fight_idx,
                page_entries=self.overflow_entries,
                db_session_factory=self.db_session_factory,
                overflow_entries=[],
                partial_scores={**self.partial_scores, **new_scores},
                result_message_id=self.result_message_id,
                result_channel_id=self.result_channel_id,
            )
            try:
                await interaction.response.send_modal(modal2)
            except discord.NotFound as exc:
                if exc.code == 10062:
                    log.warning(
                        "Interaction expired chaining ScoreModal page 2 (lobby=%s, fight=%s, user=%s)",
                        self.lobby_id, self.fight_idx, interaction.user.id,
                    )
                else:
                    log.error(
                        "NotFound chaining ScoreModal page 2 (lobby=%s, fight=%s, user=%s): %s",
                        self.lobby_id, self.fight_idx, interaction.user.id, exc,
                    )
            except discord.HTTPException as exc:
                log.error(
                    "HTTP error chaining ScoreModal page 2 (lobby=%s, fight=%s, user=%s): %s",
                    self.lobby_id, self.fight_idx, interaction.user.id, exc,
                )
            return

        # Final page – save all scores
        all_scores = {**self.partial_scores, **new_scores}
        fight_key = f"fight_{self.fight_idx}"

        tier = ""
        lobby_num = 0
        match_id = 0

        try:
            with self.db_session_factory() as session:
                lobby = session.get(Lobby, self.lobby_id)
                if lobby is None:
                    await _safe_send(
                        interaction, f"ScoreModal.on_submit lobby={self.lobby_id}",
                        "❌ Lobby không tồn tại.", ephemeral=True,
                    )
                    return

                existing_scores = dict(lobby.scores or {})
                existing_scores[fight_key] = all_scores
                lobby.scores = existing_scores
                session.commit()

                tier = lobby.tier or ""
                lobby_num = lobby.lobby_number or 0
                match_id = lobby.match_id
        except Exception as exc:
            log.exception(
                "DB error saving scores in ScoreModal (lobby=%s, fight=%s, user=%s)",
                self.lobby_id, self.fight_idx, interaction.user.id,
            )
            await _safe_send(
                interaction, f"ScoreModal.on_submit lobby={self.lobby_id}",
                "❌ Đã xảy ra lỗi nội bộ khi lưu kết quả.", ephemeral=True,
            )
            return

        await _safe_send(
            interaction, f"ScoreModal.on_submit lobby={self.lobby_id}",
            f"✅ Đã lưu kết quả Trận {self.fight_idx} "
            f"cho Lobby {tier} #{lobby_num}!",
            ephemeral=True,
        )

        # Update the result embed in the result channel
        if self.result_channel_id and self.result_message_id:
            ch = interaction.client.get_channel(self.result_channel_id)
            if ch:
                try:
                    msg = await ch.fetch_message(self.result_message_id)
                    new_embed = None
                    with self.db_session_factory() as session:
                        lobby = session.get(Lobby, self.lobby_id)
                        match = session.get(Match, match_id)
                        if lobby and match:
                            _p_map = _load_player_map(session, lobby.users_list or [])
                            new_embed = build_lobby_result_embed(lobby, match, _p_map)
                    if new_embed:
                        await msg.edit(embed=new_embed)
                except discord.NotFound as exc:
                    log.warning(
                        "Result message not found when updating scores "
                        "(lobby=%s, msg=%s): %s",
                        self.lobby_id, self.result_message_id, exc,
                    )
                except discord.HTTPException as exc:
                    log.error(
                        "HTTP error updating result embed (lobby=%s, msg=%s): %s",
                        self.lobby_id, self.result_message_id, exc,
                    )
                except Exception as exc:
                    log.exception(
                        "Unexpected error updating result embed (lobby=%s)",
                        self.lobby_id,
                    )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception(
            "Unhandled error in ScoreModal (lobby=%s, fight=%s, user=%s)",
            self.lobby_id, self.fight_idx, interaction.user.id,
        )
        if not interaction.response.is_done():
            await _safe_send(
                interaction, f"ScoreModal.on_error lobby={self.lobby_id}",
                "❌ Đã xảy ra lỗi không mong muốn.", ephemeral=True,
            )


# ── Result view (fight buttons + cancel + finalize) ───────────────────────────


class LobbyResultView(discord.ui.View):
    """Persistent view attached to each lobby's result-entry embed.

    Buttons
    -------
    - N blue buttons labelled "Trận N map_name" (one per fight)
    - 1 red  "Hủy Lobby" button
    - 1 green "Chốt Kết Quả" button

    Only server administrators may interact with these buttons.
    """

    def __init__(
        self,
        lobby_id: int,
        count_fight: int,
        map_names: list[str],
        db_session_factory,
    ) -> None:
        super().__init__(timeout=None)
        self.lobby_id = lobby_id
        self.count_fight = count_fight
        self.map_names = map_names
        self.db_session_factory = db_session_factory

        # Row 0: fight buttons (up to 5; count_fight is capped at 5 by open_registration)
        for i in range(1, count_fight + 1):
            map_name = map_names[i - 1] if i - 1 < len(map_names) else f"map{i}"
            btn = discord.ui.Button(
                label=f"Trận {i} {map_name}",
                style=discord.ButtonStyle.primary,
                row=0,
            )
            btn.callback = self._make_fight_callback(i)
            self.add_item(btn)

        # Row 1: management buttons
        cancel_btn = discord.ui.Button(
            label="Hủy Lobby",
            style=discord.ButtonStyle.danger,
            row=1,
        )
        cancel_btn.callback = self._cancel_lobby
        self.add_item(cancel_btn)

        finalize_btn = discord.ui.Button(
            label="Chốt Kết Quả ✅",
            style=discord.ButtonStyle.success,
            row=1,
        )
        finalize_btn.callback = self._finalize_lobby
        self.add_item(finalize_btn)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _make_fight_callback(self, fight_idx: int):
        async def callback(interaction: discord.Interaction) -> None:
            await self._open_score_modal(interaction, fight_idx)
        return callback

    async def _check_admin(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            await _safe_send(
                interaction, f"LobbyResultView._check_admin lobby={self.lobby_id}",
                "❌ Chỉ admin mới có thể sử dụng chức năng này.", ephemeral=True,
            )
            return False
        return True

    async def _open_score_modal(
        self, interaction: discord.Interaction, fight_idx: int
    ) -> None:
        if not await self._check_admin(interaction):
            return

        from entity import Lobby, User
        from config import RESULT_CHANNEL_ID

        try:
            with self.db_session_factory() as session:
                lobby = session.get(Lobby, self.lobby_id)
                if lobby is None:
                    await _safe_send(
                        interaction, f"LobbyResultView._open_score_modal lobby={self.lobby_id}",
                        "❌ Lobby không tồn tại.", ephemeral=True,
                    )
                    return
                if lobby.status != "active":
                    await _safe_send(
                        interaction, f"LobbyResultView._open_score_modal lobby={self.lobby_id}",
                        f"⚠️ Lobby này đã ở trạng thái **{lobby.status}**.", ephemeral=True,
                    )
                    return

                users_list: list = lobby.users_list or []
                result_msg_id = lobby.result_message_id
                civs_dict: dict = lobby.civs or {}
                users = (
                    session.query(User).filter(User.id.in_(users_list)).all()
                    if users_list
                    else []
                )
                p_map = {u.id: (u.ingame_name or f"User{u.id}") for u in users}
        except Exception as exc:
            log.exception(
                "DB error in _open_score_modal (lobby=%s, fight=%s, user=%s)",
                self.lobby_id, fight_idx, interaction.user.id,
            )
            if not interaction.response.is_done():
                await _safe_send(
                    interaction, f"LobbyResultView._open_score_modal lobby={self.lobby_id}",
                    "❌ Đã xảy ra lỗi nội bộ.", ephemeral=True,
                )
            return

        # Build page entries: (str_key, display_label)
        # Label shows "IngameName (emoji)" so the judge knows which civ each player uses.
        entries: list[tuple[str, str]] = []
        for uid in users_list:
            ingame_name = p_map.get(uid, f"User{uid}")
            user_civs = civs_dict.get(str(uid), [])
            fight_civ = user_civs[fight_idx - 1] if fight_idx - 1 < len(user_civs) else ""
            label = f"{ingame_name} ({fight_civ})" if fight_civ else ingame_name
            entries.append((str(uid), label))

        page1 = entries[:_MODAL_PAGE_SIZE]
        overflow = entries[_MODAL_PAGE_SIZE:]

        modal = ScoreModal(
            lobby_id=self.lobby_id,
            fight_idx=fight_idx,
            page_entries=page1,
            db_session_factory=self.db_session_factory,
            overflow_entries=overflow,
            partial_scores={},
            result_message_id=result_msg_id,
            result_channel_id=RESULT_CHANNEL_ID,
        )
        try:
            await interaction.response.send_modal(modal)
        except discord.NotFound as exc:
            if exc.code == 10062:
                log.warning(
                    "Interaction expired opening ScoreModal (lobby=%s, fight=%s, user=%s)",
                    self.lobby_id, fight_idx, interaction.user.id,
                )
            else:
                log.error(
                    "NotFound opening ScoreModal (lobby=%s, fight=%s, user=%s): %s",
                    self.lobby_id, fight_idx, interaction.user.id, exc,
                )
        except discord.HTTPException as exc:
            log.error(
                "HTTP error opening ScoreModal (lobby=%s, fight=%s, user=%s): %s",
                self.lobby_id, fight_idx, interaction.user.id, exc,
            )

    async def _cancel_lobby(self, interaction: discord.Interaction) -> None:
        if not await self._check_admin(interaction):
            return

        from entity import Lobby, Match
        from helpers import now_vn

        try:
            with self.db_session_factory() as session:
                lobby = session.get(Lobby, self.lobby_id)
                if lobby is None:
                    await _safe_send(
                        interaction, f"LobbyResultView._cancel_lobby lobby={self.lobby_id}",
                        "❌ Lobby không tồn tại.", ephemeral=True,
                    )
                    return
                if lobby.status != "active":
                    await _safe_send(
                        interaction, f"LobbyResultView._cancel_lobby lobby={self.lobby_id}",
                        f"⚠️ Lobby đã ở trạng thái **{lobby.status}**.", ephemeral=True,
                    )
                    return

                lobby.status = "cancelled"
                session.commit()
                match = session.get(Match, lobby.match_id)

                # If all lobbies of this match are now in a terminal state, mark the
                # match as ended so the message-cleanup scheduler can pick it up.
                remaining_active = (
                    session.query(Lobby)
                    .filter(Lobby.match_id == lobby.match_id, Lobby.status == "active")
                    .count()
                )
                if remaining_active == 0 and match and match.end_time is None:
                    match.end_time = now_vn()
                    session.commit()

                _uids = lobby.users_list or []
                _p_map = _load_player_map(session, _uids)
                new_embed = build_lobby_result_embed(lobby, match, _p_map) if match else None
        except Exception as exc:
            log.exception(
                "DB error in _cancel_lobby (lobby=%s, user=%s)",
                self.lobby_id, interaction.user.id,
            )
            if not interaction.response.is_done():
                await _safe_send(
                    interaction, f"LobbyResultView._cancel_lobby lobby={self.lobby_id}",
                    "❌ Đã xảy ra lỗi nội bộ.", ephemeral=True,
                )
            return

        if new_embed:
            await _safe_edit(
                interaction, f"LobbyResultView._cancel_lobby lobby={self.lobby_id}",
                embed=new_embed, view=discord.ui.View(),
            )
        else:
            await _safe_send(
                interaction, f"LobbyResultView._cancel_lobby lobby={self.lobby_id}",
                "✅ Lobby đã bị hủy.", ephemeral=True,
            )

    async def _finalize_lobby(self, interaction: discord.Interaction) -> None:
        if not await self._check_admin(interaction):
            return

        from entity import Lobby, Match
        from helpers import now_vn

        try:
            with self.db_session_factory() as session:
                lobby = session.get(Lobby, self.lobby_id)
                if lobby is None:
                    await _safe_send(
                        interaction, f"LobbyResultView._finalize_lobby lobby={self.lobby_id}",
                        "❌ Lobby không tồn tại.", ephemeral=True,
                    )
                    return
                if lobby.status != "active":
                    await _safe_send(
                        interaction, f"LobbyResultView._finalize_lobby lobby={self.lobby_id}",
                        f"⚠️ Lobby đã ở trạng thái **{lobby.status}**.", ephemeral=True,
                    )
                    return

                # Verify all fights have scores entered
                scores: dict = lobby.scores or {}
                match = session.get(Match, lobby.match_id)
                count_fight = match.count_fight if match else self.count_fight

                missing_fights = [
                    i for i in range(1, count_fight + 1)
                    if not scores.get(f"fight_{i}")
                ]
                if missing_fights:
                    missing_str = ", ".join(f"Trận {i}" for i in missing_fights)
                    await _safe_send(
                        interaction, f"LobbyResultView._finalize_lobby lobby={self.lobby_id}",
                        f"⚠️ Chưa nhập kết quả cho: **{missing_str}**. "
                        "Vui lòng nhập đủ trước khi chốt.",
                        ephemeral=True,
                    )
                    return

                lobby.status = "finished"
                session.commit()

                # If all lobbies of this match are now in a terminal state, mark the
                # match as ended so the message-cleanup scheduler can pick it up.
                remaining_active = (
                    session.query(Lobby)
                    .filter(Lobby.match_id == lobby.match_id, Lobby.status == "active")
                    .count()
                )
                if remaining_active == 0 and match and match.end_time is None:
                    match.end_time = now_vn()
                    session.commit()

                _uids = lobby.users_list or []
                _p_map = _load_player_map(session, _uids)
                new_embed = build_lobby_result_embed(lobby, match, _p_map) if match else None
        except Exception as exc:
            log.exception(
                "DB error in _finalize_lobby (lobby=%s, user=%s)",
                self.lobby_id, interaction.user.id,
            )
            if not interaction.response.is_done():
                await _safe_send(
                    interaction, f"LobbyResultView._finalize_lobby lobby={self.lobby_id}",
                    "❌ Đã xảy ra lỗi nội bộ.", ephemeral=True,
                )
            return

        if new_embed:
            await _safe_edit(
                interaction, f"LobbyResultView._finalize_lobby lobby={self.lobby_id}",
                embed=new_embed, view=discord.ui.View(),
            )
        else:
            await _safe_send(
                interaction, f"LobbyResultView._finalize_lobby lobby={self.lobby_id}",
                "✅ Lobby đã được chốt kết quả.", ephemeral=True,
            )

