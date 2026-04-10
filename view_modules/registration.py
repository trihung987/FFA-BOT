from __future__ import annotations

from datetime import datetime

import discord

from config import SHOWMATCH_ROLE_ID as _SHOWMATCH_ROLE_ID
from helpers import format_vn_time, now_vn, parse_duration

from .common import (
    _load_player_map,
    _safe_edit,
    _safe_send,
    build_checkin_embed,
    build_registration_embed,
    log,
)


class MapNamesModal(discord.ui.Modal):
    """Popup modal that collects one map name per fight and opens registration."""

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

        try:
            time_start_dt = datetime.strptime(self.time_start, "%Y-%m-%d %H:%M")
        except ValueError:
            await _safe_send(
                interaction,
                "MapNamesModal.on_submit",
                "❌ Định dạng thời gian không hợp lệ. Vui lòng dùng: YYYY-MM-DD HH:MM",
                ephemeral=True,
            )
            return

        try:
            checkin_delta = parse_duration(self.time_reach_checkin)
        except ValueError:
            await _safe_send(
                interaction,
                "MapNamesModal.on_submit",
                f"❌ Định dạng thời gian check-in không hợp lệ: {self.time_reach_checkin!r}. Vui lòng dùng: 1h hoặc 30p",
                ephemeral=True,
            )
            return

        try:
            divide_delta = parse_duration(self.time_reach_divide_lobby)
        except ValueError:
            await _safe_send(
                interaction,
                "MapNamesModal.on_submit",
                f"❌ Định dạng thời gian chia lobby không hợp lệ: {self.time_reach_divide_lobby!r}. Vui lòng dùng: 1h hoặc 30p",
                ephemeral=True,
            )
            return

        if checkin_delta <= divide_delta:
            await _safe_send(
                interaction,
                "MapNamesModal.on_submit",
                "❌ Thời gian mở check-in phải lớn hơn thời gian chia lobby. "
                f"({self.time_reach_checkin} phải trước {self.time_reach_divide_lobby})",
                ephemeral=True,
            )
            return

        now = now_vn()
        checkin_open_dt = time_start_dt - checkin_delta
        divide_dt = time_start_dt - divide_delta

        if time_start_dt <= now:
            await _safe_send(
                interaction,
                "MapNamesModal.on_submit",
                "❌ Giờ bắt đầu trận phải ở tương lai.",
                ephemeral=True,
            )
            return

        if divide_dt <= now:
            await _safe_send(
                interaction,
                "MapNamesModal.on_submit",
                "❌ Mốc chia lobby đã qua. Vui lòng tăng giờ bắt đầu hoặc giảm thời gian chia lobby.",
                ephemeral=True,
            )
            return

        if checkin_open_dt <= now:
            await _safe_send(
                interaction,
                "MapNamesModal.on_submit",
                "❌ Mốc mở check-in đã qua. Vui lòng tăng giờ bắt đầu hoặc giảm thời gian mở check-in.",
                ephemeral=True,
            )
            return

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
        except Exception:
            log.exception(
                "DB error creating match in MapNamesModal.on_submit (user=%s)",
                interaction.user.id,
            )
            await _safe_send(
                interaction,
                "MapNamesModal.on_submit",
                "❌ Đã xảy ra lỗi nội bộ khi tạo trận.",
                ephemeral=True,
            )
            return

        view = RegistrationView(match_id=match_id, db_session_factory=self.db_session_factory)
        role_mention = f"<@&{_SHOWMATCH_ROLE_ID}>" if _SHOWMATCH_ROLE_ID else None
        try:
            reg_msg = await self.register_channel.send(content=role_mention, embed=embed, view=view)
        except discord.HTTPException:
            log.exception(
                "Failed to send registration message for match #%s (user=%s)",
                match_id,
                interaction.user.id,
            )
            await _safe_send(
                interaction,
                "MapNamesModal.on_submit",
                "❌ Không thể gửi thông báo đăng ký. Vui lòng thử lại.",
                ephemeral=True,
            )
            return

        try:
            with self.db_session_factory() as session:
                db_match = session.get(Match, match_id)
                if db_match is not None:
                    db_match.register_message_id = reg_msg.id
                    session.commit()
        except Exception:
            log.exception("DB error saving register_message_id for match #%s", match_id)

        await _safe_send(
            interaction,
            "MapNamesModal.on_submit",
            f"✅ Đã mở đăng ký cho trận #{match_id}!",
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Unhandled error in MapNamesModal (user=%s)", interaction.user.id)
        if not interaction.response.is_done():
            await _safe_send(
                interaction,
                "MapNamesModal.on_error",
                "❌ Đã xảy ra lỗi không mong muốn.",
                ephemeral=True,
            )


def build_disabled_registration_view() -> discord.ui.View:
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Tham gia", style=discord.ButtonStyle.success, emoji="✅", disabled=True))
    view.add_item(discord.ui.Button(label="Hủy đăng ký", style=discord.ButtonStyle.danger, emoji="❌", disabled=True))
    return view


def build_disabled_checkin_view() -> discord.ui.View:
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Sẵn sàng ✅", style=discord.ButtonStyle.primary, disabled=True))
    return view


class RegistrationView(discord.ui.View):
    def __init__(self, match_id: int, db_session_factory) -> None:
        super().__init__(timeout=None)
        self.match_id = match_id
        self.db_session_factory = db_session_factory

    @discord.ui.button(label="Tham gia", style=discord.ButtonStyle.success, emoji="✅")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from entity import Match, User

        user_id = interaction.user.id

        try:
            with self.db_session_factory() as session:
                player = session.get(User, user_id)
                if player is None:
                    await _safe_send(
                        interaction,
                        f"RegistrationView.join match={self.match_id}",
                        "❌ Bạn chưa có hồ sơ FFA. Vui lòng dùng `/set_ingame_name` trước khi đăng ký.",
                        ephemeral=True,
                    )
                    return

                match: Match | None = session.get(Match, self.match_id)
                if match is None:
                    await _safe_send(interaction, f"RegistrationView.join match={self.match_id}", "❌ Trận không tồn tại.", ephemeral=True)
                    return

                current_status = match.status or "open"
                if current_status != "open":
                    await _safe_send(
                        interaction,
                        f"RegistrationView.join match={self.match_id}",
                        "⚠️ Đăng ký trận đã đóng, bạn không thể tham gia thêm.",
                        ephemeral=True,
                    )
                    return

                registered: list = (match.register_users_id or []).copy()
                if user_id in registered:
                    await _safe_send(interaction, f"RegistrationView.join match={self.match_id}", "⚠️ Bạn đã đăng ký rồi!", ephemeral=True)
                    return

                registered.append(user_id)
                match.register_users_id = registered
                session.commit()
                session.refresh(match)

                p_map = _load_player_map(session, registered)
                new_embed = build_registration_embed(match, p_map)
        except Exception:
            log.exception("Error in RegistrationView.join (match=%s, user=%s)", self.match_id, user_id)
            if not interaction.response.is_done():
                await _safe_send(interaction, f"RegistrationView.join match={self.match_id}", "❌ Đã xảy ra lỗi nội bộ.", ephemeral=True)
            return

        await _safe_edit(interaction, f"RegistrationView.join match={self.match_id}", embed=new_embed, view=self)

    @discord.ui.button(label="Hủy đăng ký", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from entity import Match

        user_id = interaction.user.id

        try:
            with self.db_session_factory() as session:
                match: Match | None = session.get(Match, self.match_id)
                if match is None:
                    await _safe_send(interaction, f"RegistrationView.cancel match={self.match_id}", "❌ Trận không tồn tại.", ephemeral=True)
                    return

                current_status = match.status or "open"
                if current_status != "open":
                    await _safe_send(
                        interaction,
                        f"RegistrationView.cancel match={self.match_id}",
                        "⚠️ Đăng ký trận đã đóng, bạn không thể hủy đăng ký lúc này.",
                        ephemeral=True,
                    )
                    return

                registered: list = (match.register_users_id or []).copy()
                if user_id not in registered:
                    await _safe_send(interaction, f"RegistrationView.cancel match={self.match_id}", "⚠️ Bạn chưa đăng ký!", ephemeral=True)
                    return

                registered.remove(user_id)
                match.register_users_id = registered
                session.commit()
                session.refresh(match)

                p_map = _load_player_map(session, registered)
                new_embed = build_registration_embed(match, p_map)
        except Exception:
            log.exception("Error in RegistrationView.cancel (match=%s, user=%s)", self.match_id, user_id)
            if not interaction.response.is_done():
                await _safe_send(interaction, f"RegistrationView.cancel match={self.match_id}", "❌ Đã xảy ra lỗi nội bộ.", ephemeral=True)
            return

        await _safe_edit(interaction, f"RegistrationView.cancel match={self.match_id}", embed=new_embed, view=self)


class CheckInView(discord.ui.View):
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
                player = session.get(User, user_id)
                if player is None:
                    await _safe_send(
                        interaction,
                        f"CheckInView.ready match={self.match_id}",
                        "❌ Bạn chưa có hồ sơ FFA. Vui lòng dùng `/set_ingame_name` trước khi check-in.",
                        ephemeral=True,
                    )
                    return

                match: Match | None = session.get(Match, self.match_id)
                if match is None:
                    await _safe_send(interaction, f"CheckInView.ready match={self.match_id}", "❌ Trận không tồn tại.", ephemeral=True)
                    return

                current_status = match.status or "open"
                if current_status != "checkin":
                    await _safe_send(
                        interaction,
                        f"CheckInView.ready match={self.match_id}",
                        f"⚠️ Trận hiện không ở giai đoạn check-in (trạng thái: {current_status}).",
                        ephemeral=True,
                    )
                    return

                registered: list = match.register_users_id or []
                if user_id not in registered:
                    await _safe_send(
                        interaction,
                        f"CheckInView.ready match={self.match_id}",
                        "⚠️ Bạn chưa đăng ký tham gia trận này!",
                        ephemeral=True,
                    )
                    return

                try:
                    now = now_vn()
                    checkin_open = match.time_start - parse_duration(match.time_reach_checkin)
                    divide_time = match.time_start - parse_duration(match.time_reach_divide_lobby)
                    if now < checkin_open:
                        await _safe_send(
                            interaction,
                            f"CheckInView.ready match={self.match_id}",
                            f"⏳ Chưa đến giờ check-in! Giờ mở check-in: {format_vn_time(checkin_open)}",
                            ephemeral=True,
                        )
                        return
                    if now >= divide_time:
                        await _safe_send(
                            interaction,
                            f"CheckInView.ready match={self.match_id}",
                            f"⌛ Đã hết thời gian check-in! Check-in kết thúc lúc: {format_vn_time(divide_time)}",
                            ephemeral=True,
                        )
                        return
                except ValueError:
                    log.warning(
                        "CheckInView.ready: invalid check-in window config for match #%s",
                        self.match_id,
                    )
                    await _safe_send(
                        interaction,
                        f"CheckInView.ready match={self.match_id}",
                        "❌ Cấu hình thời gian check-in của trận không hợp lệ. Vui lòng liên hệ admin.",
                        ephemeral=True,
                    )
                    return

                checked_in: list = (match.checkin_users_id or []).copy()
                if user_id in checked_in:
                    await _safe_send(interaction, f"CheckInView.ready match={self.match_id}", "⚠️ Bạn đã check-in rồi!", ephemeral=True)
                    return

                checked_in.append(user_id)
                match.checkin_users_id = checked_in
                session.commit()
                session.refresh(match)

                all_user_ids = list(set((match.register_users_id or []) + checked_in))
                p_map = _load_player_map(session, all_user_ids)
                new_embed = build_checkin_embed(match, p_map)
        except Exception:
            log.exception("Error in CheckInView.ready (match=%s, user=%s)", self.match_id, user_id)
            if not interaction.response.is_done():
                await _safe_send(interaction, f"CheckInView.ready match={self.match_id}", "❌ Đã xảy ra lỗi nội bộ.", ephemeral=True)
            return

        await _safe_edit(interaction, f"CheckInView.ready match={self.match_id}", embed=new_embed, view=self)
