from __future__ import annotations

import asyncio
import hashlib
import json
import os
from io import BytesIO
from pathlib import Path

import discord

from .common import (
    _load_player_map,
    _safe_defer,
    _safe_edit_original_response,
    _safe_message_edit,
    _safe_send,
    _set_view_items_disabled,
    log,
)


FONT_BASE_DIR = Path(__file__).resolve().parent.parent / "Be_Vietnam_Pro,Noto_Sans,Poppins"
BE_VIETNAM_PRO_DIR = FONT_BASE_DIR / "Be_Vietnam_Pro"
NOTO_SANS_DIR = FONT_BASE_DIR / "Noto_Sans" / "static"

_FONT_CACHE: dict[tuple[int, bool], object] = {}
_LOBBY_IMAGE_CACHE: dict[int, tuple[str, bytes]] = {}
_MAX_IMAGE_CACHE_ITEMS = 256


def build_lobby_result_embed(lobby, match, p_map: dict[int, str] | None = None) -> discord.Embed:
    from lobby_division import TIER_EMOJI

    tier: str = lobby.tier or ""
    emoji = TIER_EMOJI.get(tier, "🎮")

    status = lobby.status or "active"
    if status == "finished":
        title = f"{emoji} Kết Quả Trận `#{match.id}` - Lobby {tier} #{lobby.lobby_number}"
    elif status == "cancelled":
        title = f"{emoji} Đã Hủy Kết Quả Trận `#{match.id}` - Lobby {tier} #{lobby.lobby_number}"
    else:
        title = f"{emoji} Nhập Kết Quả Trận `#{match.id}` - Lobby {tier} #{lobby.lobby_number}"

    status_labels = {
        "active": "🟢 Đang diễn ra",
        "cancelled": "🔴 Đã hủy",
        "finished": "✅ Đã kết thúc",
    }
    status_str = status_labels.get(status, status)

    map_names: list = match.name_maps or []
    count_fight: int = match.count_fight
    map_order = ", ".join(f"T{i}: {name}" for i, name in enumerate(map_names[:count_fight], start=1)) or "N/A"

    lines = [
        f"🆔 **Trận:** #{match.id}",
        f"📊 **Trạng thái:** {status_str}",
        f"🎯 **Số trận:** {count_fight}",
        f"🗺️ **Thứ tự map:** {map_order}",
        "",
        "📌 Bảng điểm được hiển thị trong ảnh bên dưới.",
    ]

    if status == "finished":
        color = discord.Color.green()
    elif status == "cancelled":
        color = discord.Color.red()
    else:
        color = discord.Color.orange()

    embed = discord.Embed(title=title, description="\n".join(lines), color=color)
    embed.set_image(url=f"attachment://{_build_lobby_score_image_filename(lobby)}")
    embed.set_footer(text=f"Lobby ID: {lobby.id} | Trận #{match.id}")
    return embed


def _build_lobby_score_image_filename(lobby) -> str:
    return f"lobby_score_{getattr(lobby, 'id', 'unknown')}.png"


def _safe_int_score(raw) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _load_font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    key = (size, bold)
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached

    candidates = [
        BE_VIETNAM_PRO_DIR / ("BeVietnamPro-Bold.ttf" if bold else "BeVietnamPro-Regular.ttf"),
        NOTO_SANS_DIR / ("NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf"),
    ]
    for path in candidates:
        if path.exists():
            try:
                loaded = ImageFont.truetype(str(path), size)
                _FONT_CACHE[key] = loaded
                return loaded
            except Exception:
                continue
    loaded = ImageFont.load_default()
    _FONT_CACHE[key] = loaded
    return loaded


def _warm_lobby_result_font_cache() -> None:
    # Preload sizes used by lobby result rendering so first render is faster.
    try:
        _load_font(44, bold=True)
        _load_font(24, bold=True)
        _load_font(22, bold=False)
    except Exception:
        # Keep module import safe even when Pillow/fonts are not available.
        log.warning("Unable to preload lobby result fonts.")


def _build_lobby_score_fingerprint(lobby, match, p_map: dict[int, str] | None = None) -> str:
    payload = {
        "lobby_id": getattr(lobby, "id", None),
        "lobby_number": getattr(lobby, "lobby_number", None),
        "tier": getattr(lobby, "tier", None),
        "users_list": list(getattr(lobby, "users_list", []) or []),
        "scores": getattr(lobby, "scores", {}) or {},
        "count_fight": int(getattr(match, "count_fight", 0) or 0),
        "players": p_map or {},
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_lobby_result_image_file(lobby, match, p_map: dict[int, str] | None = None) -> discord.File | None:
    try:
        from PIL import Image, ImageDraw
    except Exception:
        log.exception("Pillow is unavailable while building lobby score image (lobby=%s)", getattr(lobby, "id", None))
        return None

    lobby_id = getattr(lobby, "id", None)
    fingerprint = _build_lobby_score_fingerprint(lobby, match, p_map)
    if isinstance(lobby_id, int):
        cached = _LOBBY_IMAGE_CACHE.get(lobby_id)
        if cached is not None and cached[0] == fingerprint:
            return discord.File(BytesIO(cached[1]), filename=_build_lobby_score_image_filename(lobby))

    bg_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "background",
        "AoEIV-DynastiesOfTheEast-ForestLords-1920x1080-1.webp",
    )

    width, height = 1600, 900
    try:
        if os.path.exists(bg_path):
            base = Image.open(bg_path).convert("RGBA").resize((width, height))
        else:
            log.warning("Background image not found for lobby score board: %s", bg_path)
            base = Image.new("RGBA", (width, height), (28, 40, 33, 255))
    except Exception:
        log.exception("Failed to open/resize background image for lobby=%s", getattr(lobby, "id", None))
        base = Image.new("RGBA", (width, height), (28, 40, 33, 255))

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font_title = _load_font(44, bold=True)
    font_header = _load_font(24, bold=True)
    font_cell = _load_font(22)

    tier = lobby.tier or "?"
    lobby_number = lobby.lobby_number or 0
    title_text = f"Điểm số lobby {tier} #{lobby_number}"

    panel_margin_x = 70
    panel_top = 80
    panel_bottom = height - 70
    draw.rounded_rectangle(
        (panel_margin_x, panel_top, width - panel_margin_x, panel_bottom),
        radius=28,
        fill=(10, 16, 12, 185),
        outline=(214, 196, 146, 230),
        width=3,
    )

    draw.text((panel_margin_x + 30, panel_top + 24), title_text, fill=(255, 244, 214, 255), font=font_title)

    users_list: list[int] = lobby.users_list or []
    count_fight = int(match.count_fight or 0)
    scores: dict = lobby.scores or {}

    headers = ["#", "Người chơi"] + [f"T{i}" for i in range(1, count_fight + 1)] + ["Tổng"]

    player_rows: list[tuple[str, list[int], int]] = []
    for uid in users_list:
        player_name = p_map.get(uid, "Unknown") if p_map else "Unknown"
        row_scores: list[int] = []
        for i in range(1, count_fight + 1):
            fight_scores = scores.get(f"fight_{i}", {}) or {}
            row_scores.append(_safe_int_score(fight_scores.get(str(uid), 0)))
        total_score = sum(row_scores)
        player_rows.append((player_name, row_scores, total_score))

    player_rows.sort(key=lambda item: (-item[2], item[0].lower()))

    rows: list[list[str]] = []
    for rank, (player_name, row_scores, total_score) in enumerate(player_rows, start=1):
        rows.append([str(rank), player_name, *[str(v) for v in row_scores], str(total_score)])

    if not rows:
        rows.append(["1", "(Chưa có người chơi)", *(["0"] * count_fight), "0"])

    table_left = panel_margin_x + 24
    table_right = width - panel_margin_x - 24
    table_top = panel_top + 110
    row_h = 42
    table_width = table_right - table_left

    fights_width = 84 * count_fight
    stt_w = 60
    total_w = 90
    name_w = max(260, table_width - stt_w - total_w - fights_width)

    col_widths = [stt_w, name_w] + ([84] * count_fight) + [total_w]
    x_positions = [table_left]
    for w in col_widths:
        x_positions.append(x_positions[-1] + w)

    header_bg = (43, 63, 53, 240)
    cell_bg_odd = (21, 31, 25, 170)
    cell_bg_even = (29, 40, 32, 170)
    line_color = (180, 170, 135, 220)
    text_color = (247, 240, 220, 255)

    draw.rectangle((table_left, table_top, table_right, table_top + row_h), fill=header_bg)
    for col_idx, header in enumerate(headers):
        x0 = x_positions[col_idx]
        x1 = x_positions[col_idx + 1]
        draw.rectangle((x0, table_top, x1, table_top + row_h), outline=line_color, width=1)
        draw.text((x0 + 10, table_top + 9), header, fill=text_color, font=font_header)

    max_rows_fit = max(1, (panel_bottom - table_top - row_h - 16) // row_h)
    for row_idx, row in enumerate(rows[:max_rows_fit], start=1):
        y0 = table_top + row_h * row_idx
        y1 = y0 + row_h
        draw.rectangle((table_left, y0, table_right, y1), fill=cell_bg_even if row_idx % 2 == 0 else cell_bg_odd)
        for col_idx, value in enumerate(row):
            x0 = x_positions[col_idx]
            x1 = x_positions[col_idx + 1]
            draw.rectangle((x0, y0, x1, y1), outline=line_color, width=1)
            text = str(value)
            if col_idx == 1 and len(text) > 22:
                text = text[:21] + "…"
            draw.text((x0 + 10, y0 + 9), text, fill=text_color, font=font_cell)

    final_img = Image.alpha_composite(base, overlay).convert("RGB")
    buffer = BytesIO()
    final_img.save(buffer, format="PNG", optimize=False, compress_level=4)
    image_bytes = buffer.getvalue()

    if isinstance(lobby_id, int):
        _LOBBY_IMAGE_CACHE[lobby_id] = (fingerprint, image_bytes)
        if len(_LOBBY_IMAGE_CACHE) > _MAX_IMAGE_CACHE_ITEMS:
            _LOBBY_IMAGE_CACHE.pop(next(iter(_LOBBY_IMAGE_CACHE)))

    buffer.seek(0)
    return discord.File(buffer, filename=_build_lobby_score_image_filename(lobby))


def build_lobby_result_message_assets(lobby, match, p_map: dict[int, str] | None = None) -> tuple[discord.Embed, discord.File | None]:
    image_file = build_lobby_result_image_file(lobby, match, p_map)
    embed = build_lobby_result_embed(lobby, match, p_map)
    if image_file is None:
        embed.set_image(url=None)
    return embed, image_file


async def build_lobby_result_message_assets_async(
    lobby,
    match,
    p_map: dict[int, str] | None = None,
) -> tuple[discord.Embed, discord.File | None]:
    """Build result embed/image off the main event loop to avoid heartbeat stalls."""
    return await asyncio.to_thread(build_lobby_result_message_assets, lobby, match, p_map)


_warm_lobby_result_font_cache()


_MODAL_PAGE_SIZE = 4


def _chunk_entries(entries: list[tuple[str, str]], page_size: int) -> list[list[tuple[str, str]]]:
    if page_size <= 0:
        return [entries]
    return [entries[i:i + page_size] for i in range(0, len(entries), page_size)] or [[]]


class ScoreModalNextPageView(discord.ui.View):
    def __init__(self, allowed_user_id: int, modal_factory, *, timeout: float = 300) -> None:
        super().__init__(timeout=timeout)
        self.allowed_user_id = allowed_user_id
        self.modal_factory = modal_factory
        self._opening = False

    @discord.ui.button(label="Mở trang tiếp theo", style=discord.ButtonStyle.primary)
    async def open_next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.allowed_user_id:
            await _safe_send(
                interaction,
                "ScoreModalNextPageView.open_next_page",
                "❌ Bạn không thể mở phiên nhập điểm của người khác.",
                ephemeral=True,
            )
            return

        if self._opening:
            await _safe_send(
                interaction,
                "ScoreModalNextPageView.open_next_page",
                "⏳ Đang mở trang tiếp theo, vui lòng đợi.",
                ephemeral=True,
            )
            return
        self._opening = True

        modal = self.modal_factory()
        try:
            await interaction.response.send_modal(modal)
        except discord.NotFound as exc:
            if exc.code == 10062:
                log.warning(
                    "Interaction expired opening next score page (user=%s)",
                    interaction.user.id,
                )
            else:
                log.error(
                    "NotFound opening next score page (user=%s): %s",
                    interaction.user.id,
                    exc,
                )
        except discord.HTTPException as exc:
            log.error(
                "HTTP error opening next score page (user=%s): %s",
                interaction.user.id,
                exc,
            )
        finally:
            self._opening = False


class ScoreModal(discord.ui.Modal):
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
        result_view=None,
        page_index: int = 1,
        total_pages: int = 1,
    ) -> None:
        page_suffix = f" ({page_index}/{total_pages})" if total_pages > 1 else ""
        super().__init__(title=f"Nhập điểm Trận {fight_idx}{page_suffix}")
        self.lobby_id = lobby_id
        self.fight_idx = fight_idx
        self.db_session_factory = db_session_factory
        self.overflow_entries = overflow_entries or []
        self.partial_scores: dict[str, str] = partial_scores or {}
        self.result_message_id = result_message_id
        self.result_channel_id = result_channel_id
        self.result_view = result_view
        self.page_index = page_index
        self.total_pages = total_pages

        self._inputs: list[tuple[str, discord.ui.TextInput]] = []
        for key, label in page_entries:
            inp = discord.ui.TextInput(
                label=label[:44] + "…" if len(label) > 45 else label,
                placeholder="Nhập điểm số",
                default=self.partial_scores.get(key, "0"),
                required=True,
                max_length=10,
            )
            self._inputs.append((key, inp))
            self.add_item(inp)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from entity import Lobby

        if not interaction.user.guild_permissions.administrator:
            await _safe_send(
                interaction,
                f"ScoreModal.on_submit lobby={self.lobby_id}",
                "❌ Chỉ admin mới có thể nhập và lưu kết quả.",
                ephemeral=True,
            )
            return

        new_scores = {key: inp.value for key, inp in self._inputs}

        if self.overflow_entries:
            next_page_entries = self.overflow_entries[:_MODAL_PAGE_SIZE]
            next_overflow_entries = self.overflow_entries[_MODAL_PAGE_SIZE:]
            merged_scores = {**self.partial_scores, **new_scores}

            def _build_next_modal() -> ScoreModal:
                return ScoreModal(
                    lobby_id=self.lobby_id,
                    fight_idx=self.fight_idx,
                    page_entries=next_page_entries,
                    db_session_factory=self.db_session_factory,
                    overflow_entries=next_overflow_entries,
                    partial_scores=merged_scores,
                    result_message_id=self.result_message_id,
                    result_channel_id=self.result_channel_id,
                    result_view=self.result_view,
                    page_index=self.page_index + 1,
                    total_pages=self.total_pages,
                )

            next_view = ScoreModalNextPageView(interaction.user.id, _build_next_modal)
            try:
                await _safe_send(
                    interaction,
                    f"ScoreModal.on_submit.next_page lobby={self.lobby_id}",
                    content=(
                        f"✅ Đã lưu tạm điểm trang {self.page_index}/{self.total_pages}.\n"
                        f"➡️ Nhấn **Mở trang tiếp theo** để nhập tiếp trang {self.page_index + 1}/{self.total_pages}."
                    ),
                    view=next_view,
                    ephemeral=True,
                )
            except discord.NotFound as exc:
                if exc.code == 10062:
                    log.warning(
                        "Interaction expired preparing next ScoreModal page (lobby=%s, fight=%s, user=%s)",
                        self.lobby_id,
                        self.fight_idx,
                        interaction.user.id,
                    )
                else:
                    log.error(
                        "NotFound preparing next ScoreModal page (lobby=%s, fight=%s, user=%s): %s",
                        self.lobby_id,
                        self.fight_idx,
                        interaction.user.id,
                        exc,
                    )
            except discord.HTTPException as exc:
                log.error(
                    "HTTP error preparing next ScoreModal page (lobby=%s, fight=%s, user=%s): %s",
                    self.lobby_id,
                    self.fight_idx,
                    interaction.user.id,
                    exc,
                )
            return

        all_scores = {**self.partial_scores, **new_scores}
        fight_key = f"fight_{self.fight_idx}"

        if not await _safe_defer(interaction, f"ScoreModal.on_submit lobby={self.lobby_id}", ephemeral=True, thinking=True):
            return

        tier = ""
        lobby_num = 0

        try:
            with self.db_session_factory() as session:
                lobby = session.get(Lobby, self.lobby_id)
                if lobby is None:
                    await _safe_send(interaction, f"ScoreModal.on_submit lobby={self.lobby_id}", "❌ Lobby không tồn tại.", ephemeral=True)
                    return

                existing_scores = dict(lobby.scores or {})
                existing_scores[fight_key] = all_scores
                lobby.scores = existing_scores
                session.commit()

                tier = lobby.tier or ""
                lobby_num = lobby.lobby_number or 0
        except Exception:
            log.exception(
                "DB error saving scores in ScoreModal (lobby=%s, fight=%s, user=%s)",
                self.lobby_id,
                self.fight_idx,
                interaction.user.id,
            )
            await _safe_edit_original_response(
                interaction,
                f"ScoreModal.on_submit lobby={self.lobby_id}",
                content="❌ Đã xảy ra lỗi nội bộ khi lưu kết quả.",
            )
            return

        await _safe_edit_original_response(
            interaction,
            f"ScoreModal.on_submit lobby={self.lobby_id}",
            content=(
                f"✅ Đã lưu kết quả Trận {self.fight_idx} cho Lobby {tier} #{lobby_num}!\n"
                "🔄 Nhấn **Reload** để cập nhật ảnh bảng điểm mới nhất."
            ),
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception(
            "Unhandled error in ScoreModal (lobby=%s, fight=%s, user=%s)",
            self.lobby_id,
            self.fight_idx,
            interaction.user.id,
        )
        if not interaction.response.is_done():
            await _safe_send(interaction, f"ScoreModal.on_error lobby={self.lobby_id}", "❌ Đã xảy ra lỗi không mong muốn.", ephemeral=True)


class LobbyResultView(discord.ui.View):
    def __init__(self, lobby_id: int, count_fight: int, map_names: list[str], db_session_factory) -> None:
        super().__init__(timeout=None)
        self.lobby_id = lobby_id
        self.count_fight = count_fight
        self.map_names = map_names
        self.db_session_factory = db_session_factory
        self._score_update_in_progress = False

        for i in range(1, count_fight + 1):
            map_name = map_names[i - 1] if i - 1 < len(map_names) else f"map{i}"
            btn = discord.ui.Button(label=f"Trận {i} {map_name}", style=discord.ButtonStyle.primary, row=(i - 1) // 5)
            btn.callback = self._make_fight_callback(i)
            self.add_item(btn)

        management_row = (count_fight - 1) // 5 + 1

        reload_btn = discord.ui.Button(label="Reload", style=discord.ButtonStyle.secondary, row=management_row)
        reload_btn.callback = self._reload_result
        self.add_item(reload_btn)

        cancel_btn = discord.ui.Button(label="Hủy Lobby", style=discord.ButtonStyle.danger, row=management_row)
        cancel_btn.callback = self._cancel_lobby
        self.add_item(cancel_btn)

        finalize_btn = discord.ui.Button(label="Chốt Kết Quả ✅", style=discord.ButtonStyle.success, row=management_row)
        finalize_btn.callback = self._finalize_lobby
        self.add_item(finalize_btn)

    def _set_finalize_loading_state(self, is_loading: bool) -> None:
        """Toggle finalize UI state in-place on this view."""
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.style == discord.ButtonStyle.success:
                item.label = "Đang lưu kết quả..." if is_loading else "Chốt Kết Quả ✅"
                break
        _set_view_items_disabled(self, is_loading)

    def _make_fight_callback(self, fight_idx: int):
        async def callback(interaction: discord.Interaction) -> None:
            await self._open_score_modal(interaction, fight_idx)

        return callback

    @staticmethod
    def _normalize_user_id(raw_uid) -> int | None:
        """Normalize a lobby user ID to int; return None for invalid/AI IDs."""
        try:
            uid = int(raw_uid)
        except (TypeError, ValueError):
            return None
        return uid if uid > 0 else None

    @staticmethod
    def _collect_total_scores(lobby) -> list[tuple[int, int]]:
        """Return [(user_id, total_score)] for real users in a lobby."""
        users_list: list[int] = []
        for raw_uid in lobby.users_list or []:
            uid = LobbyResultView._normalize_user_id(raw_uid)
            if uid is not None:
                users_list.append(uid)
        count_fight = 0
        scores = lobby.scores or {}
        if scores:
            count_fight = len([k for k in scores.keys() if str(k).startswith("fight_")])

        if count_fight == 0:
            return [(uid, 0) for uid in users_list]

        result: list[tuple[int, int]] = []
        for uid in users_list:
            total_score = 0
            for i in range(1, count_fight + 1):
                fight_scores = scores.get(f"fight_{i}", {}) or {}
                total_score += _safe_int_score(fight_scores.get(str(uid), 0))
            result.append((uid, total_score))
        return result

    @staticmethod
    def _compute_elo_deltas(score_rows: list[tuple[int, int]]) -> dict[int, int]:
        """Compute ELO delta per user as direct total-score addition."""
        if not score_rows:
            return {}
        return {uid: _safe_int_score(total_score) for uid, total_score in score_rows}

    @staticmethod
    def _rollup_match_status_after_lobby_resolution(session, match, now_value) -> None:
        """Update match status when all its lobbies are no longer active.

        - all cancelled -> match cancelled
        - otherwise (any finished) -> match finished
        """
        from entity import Lobby

        if match is None:
            return

        statuses = [
            str(s or "active")
            for (s,) in (
                session.query(Lobby.status)
                .filter(Lobby.match_id == match.id)
                .all()
            )
        ]
        if not statuses:
            return
        if any(status == "active" for status in statuses):
            return

        match.status = "cancelled" if all(status == "cancelled" for status in statuses) else "finished"
        if match.end_time is None:
            match.end_time = now_value

    def _apply_elo_updates_sync(self, lobby_id: int) -> None:
        """Synchronous DB write path for background ELO updates."""
        from entity import Lobby, User
        from helpers import now_vn

        with self.db_session_factory() as session:
            lobby = session.get(Lobby, lobby_id)
            if lobby is None:
                return

            score_rows = self._collect_total_scores(lobby)
            delta_by_user = self._compute_elo_deltas(score_rows)
            if not delta_by_user:
                return

            users = session.query(User).filter(User.id.in_(list(delta_by_user.keys()))).all()
            user_map = {u.id: u for u in users}
            updated_at = now_vn()
            updates: list[dict] = []

            for uid, delta in delta_by_user.items():
                user = user_map.get(uid)
                if user is None:
                    log.warning(
                        "ELO update skipped: user not found (lobby=%s, user_id=%s)",
                        lobby_id,
                        uid,
                    )
                    continue
                log.info("Applying ELO delta for user %s in lobby %s: %+d (old ELO: %s)", uid, lobby_id, delta, user.elo)
                old_elo = user.elo or 0
                new_elo = max(0, old_elo + delta)
                actual_delta = new_elo - old_elo
                updates.append(
                    {
                        "id": uid,
                        "elo": new_elo,
                        "last_elo_change": actual_delta,
                        "updated_date": updated_at,
                        "monthly_elo_gain": (user.monthly_elo_gain or 0) + actual_delta,
                    }
                )

            if not updates:
                return

            session.bulk_update_mappings(User, updates)

            session.commit()

    async def _run_elo_update_background(self, lobby_id: int) -> None:
        """Non-blocking wrapper so interaction responses are not delayed."""
        try:
            # Run in the current thread to avoid DB engine/thread affinity issues.
            self._apply_elo_updates_sync(lobby_id)
            log.info("ELO update completed in background (lobby=%s)", lobby_id)
        except Exception:
            log.exception("Background ELO update failed (lobby=%s)", lobby_id)

    async def _check_admin(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            await _safe_send(
                interaction,
                f"LobbyResultView._check_admin lobby={self.lobby_id}",
                "❌ Chỉ admin mới có thể sử dụng chức năng này.",
                ephemeral=True,
            )
            return False
        return True

    async def _reload_result(self, interaction: discord.Interaction) -> None:
        if not await self._check_admin(interaction):
            return

        if self._score_update_in_progress:
            await _safe_send(
                interaction,
                f"LobbyResultView._reload_result lobby={self.lobby_id}",
                "⏳ Hệ thống đang xử lý reload trước đó. Vui lòng đợi vài giây.",
                ephemeral=True,
            )
            return

        if not await _safe_defer(
            interaction,
            f"LobbyResultView._reload_result lobby={self.lobby_id}",
            ephemeral=True,
            thinking=True,
        ):
            return

        from entity import Lobby, Match

        self._score_update_in_progress = True
        result_msg = interaction.message
        original_embed = result_msg.embeds[0].copy() if (result_msg and result_msg.embeds) else None
        new_embed = None
        new_file = None
        reload_success = False

        if result_msg is not None:
            _set_view_items_disabled(self, True)
            loading_embed = original_embed.copy() if original_embed is not None else None
            if loading_embed is not None:
                current_footer = loading_embed.footer.text or ""
                loading_suffix = "⏳ Đang tải lại bảng điểm..."
                if loading_suffix not in current_footer:
                    loading_footer = f"{current_footer} | {loading_suffix}" if current_footer else loading_suffix
                    loading_embed.set_footer(text=loading_footer)
            lock_kwargs = {"view": self}
            if loading_embed is not None:
                lock_kwargs["embed"] = loading_embed
            await _safe_message_edit(
                result_msg,
                f"LobbyResultView._reload_result_lock lobby={self.lobby_id}",
                interaction.user.id,
                **lock_kwargs,
            )

        try:
            with self.db_session_factory() as session:
                lobby = session.get(Lobby, self.lobby_id)
                if lobby is None:
                    await _safe_edit_original_response(
                        interaction,
                        f"LobbyResultView._reload_result lobby={self.lobby_id}",
                        content="❌ Lobby không tồn tại.",
                    )
                    return

                match = session.get(Match, lobby.match_id)
                if match is None:
                    await _safe_edit_original_response(
                        interaction,
                        f"LobbyResultView._reload_result lobby={self.lobby_id}",
                        content="❌ Không tìm thấy dữ liệu trận.",
                    )
                    return

                _uids = lobby.users_list or []
                _p_map = _load_player_map(session, _uids)
                new_embed, new_file = await build_lobby_result_message_assets_async(lobby, match, _p_map)
                reload_success = True
        except Exception:
            log.exception("DB/render error in _reload_result (lobby=%s, user=%s)", self.lobby_id, interaction.user.id)
            await _safe_edit_original_response(
                interaction,
                f"LobbyResultView._reload_result lobby={self.lobby_id}",
                content="❌ Không thể reload bảng điểm lúc này.",
            )
            return
        finally:
            _set_view_items_disabled(self, False)
            self._score_update_in_progress = False

            if result_msg is not None and not reload_success:
                unlock_kwargs = {"view": self}
                if original_embed is not None:
                    unlock_kwargs["embed"] = original_embed
                await _safe_message_edit(
                    result_msg,
                    f"LobbyResultView._reload_result_unlock lobby={self.lobby_id}",
                    interaction.user.id,
                    **unlock_kwargs,
                )

        kwargs = {"embed": new_embed, "view": self}
        if new_file is not None:
            kwargs["attachments"] = [new_file]

        if result_msg is not None:
            await _safe_message_edit(
                result_msg,
                f"LobbyResultView._reload_result lobby={self.lobby_id}",
                interaction.user.id,
                **kwargs,
            )
            await _safe_edit_original_response(
                interaction,
                f"LobbyResultView._reload_result lobby={self.lobby_id}",
                content="✅ Đã reload bảng điểm mới nhất.",
            )
        else:
            await _safe_edit_original_response(
                interaction,
                f"LobbyResultView._reload_result lobby={self.lobby_id}",
                content="⚠️ Không tìm thấy tin nhắn để cập nhật bảng điểm.",
            )
            return False
        return True

    async def _open_score_modal(self, interaction: discord.Interaction, fight_idx: int) -> None:
        if not await self._check_admin(interaction):
            return

        if self._score_update_in_progress:
            await _safe_send(
                interaction,
                f"LobbyResultView._open_score_modal lobby={self.lobby_id}",
                "⏳ Hệ thống đang cập nhật điểm trận trước. Vui lòng đợi vài giây rồi thử lại.",
                ephemeral=True,
            )
            return

        from config import RESULT_CHANNEL_ID
        from entity import Lobby, User

        try:
            with self.db_session_factory() as session:
                lobby = session.get(Lobby, self.lobby_id)
                if lobby is None:
                    await _safe_send(
                        interaction,
                        f"LobbyResultView._open_score_modal lobby={self.lobby_id}",
                        "❌ Lobby không tồn tại.",
                        ephemeral=True,
                    )
                    return
                if lobby.status != "active":
                    await _safe_send(
                        interaction,
                        f"LobbyResultView._open_score_modal lobby={self.lobby_id}",
                        f"⚠️ Lobby này đã ở trạng thái **{lobby.status}**.",
                        ephemeral=True,
                    )
                    return

                users_list: list[int] = []
                for raw_uid in lobby.users_list or []:
                    uid = self._normalize_user_id(raw_uid)
                    if uid is not None:
                        users_list.append(uid)

                result_msg_id = lobby.result_message_id
                civs_dict: dict = lobby.civs or {}
                scores_dict: dict = lobby.scores or {}
                fight_scores_raw = scores_dict.get(f"fight_{fight_idx}", {}) or {}
                users = session.query(User).filter(User.id.in_(users_list)).all() if users_list else []
                p_map = {u.id: (u.ingame_name or f"User{u.id}") for u in users}
        except Exception:
            log.exception(
                "DB error in _open_score_modal (lobby=%s, fight=%s, user=%s)",
                self.lobby_id,
                fight_idx,
                interaction.user.id,
            )
            if not interaction.response.is_done():
                await _safe_send(
                    interaction,
                    f"LobbyResultView._open_score_modal lobby={self.lobby_id}",
                    "❌ Đã xảy ra lỗi nội bộ.",
                    ephemeral=True,
                )
            return

        from lobby_division import _civ_display_name

        entries: list[tuple[str, str]] = []
        partial_scores: dict[str, str] = {}
        for uid in users_list:
            ingame_name = p_map.get(uid, f"User{uid}")
            user_civs = civs_dict.get(str(uid), [])
            fight_civ = user_civs[fight_idx - 1] if fight_idx - 1 < len(user_civs) else ""
            civ_name = _civ_display_name(fight_civ) if fight_civ else ""
            label = f"{ingame_name} ({civ_name})" if civ_name else ingame_name
            entries.append((str(uid), label))
            partial_scores[str(uid)] = str(_safe_int_score(fight_scores_raw.get(str(uid), 0)))

        pages = _chunk_entries(entries, _MODAL_PAGE_SIZE)
        page1 = pages[0]
        overflow = [entry for page in pages[1:] for entry in page]
        total_pages = len(pages)

        modal = ScoreModal(
            lobby_id=self.lobby_id,
            fight_idx=fight_idx,
            page_entries=page1,
            db_session_factory=self.db_session_factory,
            overflow_entries=overflow,
            partial_scores=partial_scores,
            result_message_id=result_msg_id,
            result_channel_id=RESULT_CHANNEL_ID,
            result_view=self,
            page_index=1,
            total_pages=total_pages,
        )
        try:
            await interaction.response.send_modal(modal)
        except discord.NotFound as exc:
            if exc.code == 10062:
                log.warning(
                    "Interaction expired opening ScoreModal (lobby=%s, fight=%s, user=%s)",
                    self.lobby_id,
                    fight_idx,
                    interaction.user.id,
                )
            else:
                log.error(
                    "NotFound opening ScoreModal (lobby=%s, fight=%s, user=%s): %s",
                    self.lobby_id,
                    fight_idx,
                    interaction.user.id,
                    exc,
                )
        except discord.HTTPException as exc:
            log.error(
                "HTTP error opening ScoreModal (lobby=%s, fight=%s, user=%s): %s",
                self.lobby_id,
                fight_idx,
                interaction.user.id,
                exc,
            )

    async def _cancel_lobby(self, interaction: discord.Interaction) -> None:
        if not await self._check_admin(interaction):
            return

        if not await _safe_defer(
            interaction,
            f"LobbyResultView._cancel_lobby lobby={self.lobby_id}",
            ephemeral=True,
            thinking=True,
        ):
            return

        from entity import Lobby, Match
        from helpers import now_vn

        try:
            with self.db_session_factory() as session:
                lobby = session.get(Lobby, self.lobby_id)
                if lobby is None:
                    await _safe_edit_original_response(
                        interaction,
                        f"LobbyResultView._cancel_lobby lobby={self.lobby_id}",
                        content="❌ Lobby không tồn tại.",
                    )
                    return
                if lobby.status != "active":
                    await _safe_edit_original_response(
                        interaction,
                        f"LobbyResultView._cancel_lobby lobby={self.lobby_id}",
                        content=f"⚠️ Lobby đã ở trạng thái **{lobby.status}**.",
                    )
                    return

                lobby.status = "cancelled"
                match = session.get(Match, lobby.match_id)

                self._rollup_match_status_after_lobby_resolution(session, match, now_vn())
                session.commit()

                _uids = lobby.users_list or []
                _p_map = _load_player_map(session, _uids)
                new_embed, new_file = (
                    await build_lobby_result_message_assets_async(lobby, match, _p_map)
                    if match
                    else (None, None)
                )
        except Exception:
            log.exception("DB error in _cancel_lobby (lobby=%s, user=%s)", self.lobby_id, interaction.user.id)
            await _safe_edit_original_response(
                interaction,
                f"LobbyResultView._cancel_lobby lobby={self.lobby_id}",
                content="❌ Đã xảy ra lỗi nội bộ.",
            )
            return

        if new_embed:
            kwargs = {"embed": new_embed, "view": discord.ui.View()}
            if new_file is not None:
                kwargs["attachments"] = [new_file]
            else:
                kwargs["attachments"] = []
            if interaction.message is not None:
                await _safe_message_edit(
                    interaction.message,
                    f"LobbyResultView._cancel_lobby lobby={self.lobby_id}",
                    interaction.user.id,
                    **kwargs,
                )
            await _safe_edit_original_response(
                interaction,
                f"LobbyResultView._cancel_lobby lobby={self.lobby_id}",
                content="✅ Lobby đã bị hủy.",
            )
        else:
            await _safe_edit_original_response(
                interaction,
                f"LobbyResultView._cancel_lobby lobby={self.lobby_id}",
                content="✅ Lobby đã bị hủy.",
            )

    async def _finalize_lobby(self, interaction: discord.Interaction) -> None:
        if not await self._check_admin(interaction):
            return

        if not await _safe_defer(
            interaction,
            f"LobbyResultView._finalize_lobby lobby={self.lobby_id}",
            ephemeral=True,
            thinking=True,
        ):
            return

        if interaction.message is not None:
            self._set_finalize_loading_state(True)
            await _safe_message_edit(
                interaction.message,
                f"LobbyResultView._finalize_lobby_lock lobby={self.lobby_id}",
                interaction.user.id,
                view=self,
            )

        self._score_update_in_progress = True

        from entity import Lobby, Match
        from helpers import now_vn

        new_embed = None
        new_file = None
        try:
            with self.db_session_factory() as session:
                lobby = session.get(Lobby, self.lobby_id)
                if lobby is None:
                    await _safe_edit_original_response(
                        interaction,
                        f"LobbyResultView._finalize_lobby lobby={self.lobby_id}",
                        content="❌ Lobby không tồn tại.",
                    )
                    if interaction.message is not None:
                        self._set_finalize_loading_state(False)
                        await _safe_message_edit(
                            interaction.message,
                            f"LobbyResultView._finalize_lobby_unlock lobby={self.lobby_id}",
                            interaction.user.id,
                            view=self,
                        )
                    self._score_update_in_progress = False
                    return
                if lobby.status != "active":
                    await _safe_edit_original_response(
                        interaction,
                        f"LobbyResultView._finalize_lobby lobby={self.lobby_id}",
                        content=f"⚠️ Lobby đã ở trạng thái **{lobby.status}**.",
                    )
                    if interaction.message is not None:
                        self._set_finalize_loading_state(False)
                        await _safe_message_edit(
                            interaction.message,
                            f"LobbyResultView._finalize_lobby_unlock lobby={self.lobby_id}",
                            interaction.user.id,
                            view=self,
                        )
                    self._score_update_in_progress = False
                    return

                scores: dict = lobby.scores or {}
                match = session.get(Match, lobby.match_id)
                count_fight = match.count_fight if match else self.count_fight

                missing_fights = [i for i in range(1, count_fight + 1) if not scores.get(f"fight_{i}")]
                if missing_fights:
                    missing_str = ", ".join(f"Trận {i}" for i in missing_fights)
                    await _safe_edit_original_response(
                        interaction,
                        f"LobbyResultView._finalize_lobby lobby={self.lobby_id}",
                        content=(
                            f"⚠️ Chưa nhập kết quả cho: **{missing_str}**. "
                            "Vui lòng nhập đủ trước khi chốt."
                        ),
                    )
                    if interaction.message is not None:
                        self._set_finalize_loading_state(False)
                        await _safe_message_edit(
                            interaction.message,
                            f"LobbyResultView._finalize_lobby_unlock lobby={self.lobby_id}",
                            interaction.user.id,
                            view=self,
                        )
                    self._score_update_in_progress = False
                    return

                lobby.status = "finished"
                self._rollup_match_status_after_lobby_resolution(session, match, now_vn())
                session.commit()

                _uids = lobby.users_list or []
                _p_map = _load_player_map(session, _uids)
                new_embed, new_file = (
                    await build_lobby_result_message_assets_async(lobby, match, _p_map)
                    if match
                    else (None, None)
                )
        except Exception:
            log.exception("DB error in _finalize_lobby (lobby=%s, user=%s)", self.lobby_id, interaction.user.id)
            await _safe_edit_original_response(
                interaction,
                f"LobbyResultView._finalize_lobby lobby={self.lobby_id}",
                content="❌ Đã xảy ra lỗi nội bộ.",
            )
            if interaction.message is not None:
                self._set_finalize_loading_state(False)
                await _safe_message_edit(
                    interaction.message,
                    f"LobbyResultView._finalize_lobby_unlock lobby={self.lobby_id}",
                    interaction.user.id,
                    view=self,
                )
            self._score_update_in_progress = False
            return
        finally:
            self._score_update_in_progress = False

        if new_embed:
            kwargs = {"embed": new_embed, "view": discord.ui.View()}
            if new_file is not None:
                kwargs["attachments"] = [new_file]
            if interaction.message is not None:
                await _safe_message_edit(
                    interaction.message,
                    f"LobbyResultView._finalize_lobby lobby={self.lobby_id}",
                    interaction.user.id,
                    **kwargs,
                )
            await _safe_edit_original_response(
                interaction,
                f"LobbyResultView._finalize_lobby lobby={self.lobby_id}",
                content="✅ Lobby đã được chốt kết quả.",
            )
        else:
            await _safe_edit_original_response(
                interaction,
                f"LobbyResultView._finalize_lobby lobby={self.lobby_id}",
                content="✅ Lobby đã được chốt kết quả.",
            )

        # Run ELO update in background so finalize interaction returns quickly.
        asyncio.create_task(self._run_elo_update_background(self.lobby_id))
