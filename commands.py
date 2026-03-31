"""
Slash commands for match management.
"""

import discord
from discord import app_commands
from discord.ext import commands as ext_commands
from typing import Optional

from config import GUILD_ID, REGISTER_CHANNEL_ID
from views import MapNamesModal

guild_obj = discord.Object(id=GUILD_ID)


def register_match_commands(bot: ext_commands.Bot, db_session_factory) -> None:
    """Attach all match-related slash commands to *bot*."""

    @bot.tree.command(
        name="open_registration",
        description="Mở đăng ký cho một FFA match mới.",
        guild=guild_obj,
    )
    @app_commands.describe(
        count_fight="Số trận đánh (n)",
        time_start="Thời gian bắt đầu (định dạng: YYYY-MM-DD HH:MM)",
        time_reach_checkin="Thời gian mở check-in trước khi bắt đầu (VD: 1h hoặc 30p)",
        time_reach_divide_lobby="Thời gian chia lobby trước khi bắt đầu (VD: 30p)",
    )
    async def open_registration(
        interaction: discord.Interaction,
        count_fight: int,
        time_start: str,
        time_reach_checkin: str,
        time_reach_divide_lobby: str,
    ) -> None:
        """Open registration for a new FFA match."""

        if count_fight < 1 or count_fight > 5:
            await interaction.response.send_message(
                "❌ Số trận đánh phải từ 1 đến 5.", ephemeral=True
            )
            return

        register_channel = interaction.client.get_channel(REGISTER_CHANNEL_ID)
        if register_channel is None:
            await interaction.response.send_message(
                "❌ Không tìm thấy kênh đăng ký. Vui lòng kiểm tra cấu hình.", ephemeral=True
            )
            return

        modal = MapNamesModal(
            count_fight=count_fight,
            time_start=time_start,
            time_reach_checkin=time_reach_checkin,
            time_reach_divide_lobby=time_reach_divide_lobby,
            db_session_factory=db_session_factory,
            register_channel=register_channel,
        )
        await interaction.response.send_modal(modal)

    @bot.tree.command(
        name="set_ingame_name",
        description="Đặt tên in-game (và ELO khởi đầu nếu là admin) cho người chơi.",
        guild=guild_obj,
    )
    @app_commands.describe(
        name="Tên in-game trong game",
        elo="ELO khởi đầu (mặc định 1000, chỉ admin mới dùng được)",
    )
    async def set_ingame_name(
        interaction: discord.Interaction,
        name: str,
        elo: Optional[int] = None,
    ) -> None:
        """Allow a player to set (or update) their in-game name.

        Admins may additionally supply an *elo* value which overrides the
        player's current ELO.  For new players the ELO defaults to 1000.
        """
        from entity import User

        user_id = interaction.user.id
        is_admin = interaction.user.guild_permissions.administrator

        # Non-admins must not be able to set an explicit ELO value.
        if elo is not None and not is_admin:
            await interaction.response.send_message(
                "❌ Chỉ admin mới có thể đặt ELO.", ephemeral=True
            )
            return

        with db_session_factory() as session:
            user = session.get(User, user_id)
            if user is None:
                initial_elo = elo if elo is not None else 1000
                user = User(id=user_id, ingame_name=name, elo=initial_elo)
                session.add(user)
                msg = (
                    f"✅ Đã tạo hồ sơ: tên in-game **{name}**, ELO **{initial_elo}**."
                )
            else:
                user.ingame_name = name
                if elo is not None:
                    delta = elo - user.elo
                    user.elo = elo
                    user.last_elo_change = delta
                    if delta > 0:
                        user.monthly_elo_gain = (user.monthly_elo_gain or 0) + delta
                msg = f"✅ Đã cập nhật tên in-game: **{name}**"
                if elo is not None:
                    msg += f", ELO: **{elo}**"
                msg += "."
            session.commit()

        await interaction.response.send_message(msg, ephemeral=True)

    # ── Admin: add ticket ──────────────────────────────────────────────────────

    @bot.tree.command(
        name="add_ticket",
        description="[Admin] Thêm vé cho người chơi.",
        guild=guild_obj,
    )
    @app_commands.describe(player="Người chơi cần thêm vé", amount="Số vé cần thêm (mặc định 1)")
    @app_commands.checks.has_permissions(administrator=True)
    async def add_ticket(
        interaction: discord.Interaction,
        player: discord.Member,
        amount: int = 1,
    ) -> None:
        """Admin: add *amount* tickets to a player's account."""
        from entity import User

        if amount <= 0:
            await interaction.response.send_message(
                "❌ Số vé phải lớn hơn 0.", ephemeral=True
            )
            return

        with db_session_factory() as session:
            user = session.get(User, player.id)
            if user is None:
                await interaction.response.send_message(
                    f"❌ Người chơi {player.mention} chưa có hồ sơ. "
                    "Hãy yêu cầu họ dùng `/set_ingame_name` trước.",
                    ephemeral=True,
                )
                return
            user.ticket += amount
            new_total = user.ticket
            session.commit()

        await interaction.response.send_message(
            f"✅ Đã thêm **{amount}** vé cho {player.mention}. "
            f"Tổng vé hiện tại: **{new_total}**.",
            ephemeral=True,
        )

    # ── Admin: remove ticket ───────────────────────────────────────────────────

    @bot.tree.command(
        name="remove_ticket",
        description="[Admin] Xóa vé của người chơi.",
        guild=guild_obj,
    )
    @app_commands.describe(player="Người chơi cần xóa vé", amount="Số vé cần xóa (mặc định 1)")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_ticket(
        interaction: discord.Interaction,
        player: discord.Member,
        amount: int = 1,
    ) -> None:
        """Admin: remove *amount* tickets from a player's account."""
        from entity import User

        if amount <= 0:
            await interaction.response.send_message(
                "❌ Số vé phải lớn hơn 0.", ephemeral=True
            )
            return

        with db_session_factory() as session:
            user = session.get(User, player.id)
            if user is None:
                await interaction.response.send_message(
                    f"❌ Người chơi {player.mention} chưa có hồ sơ.",
                    ephemeral=True,
                )
                return
            if user.ticket < amount:
                await interaction.response.send_message(
                    f"❌ {player.mention} chỉ có **{user.ticket}** vé, "
                    f"không đủ để xóa **{amount}** vé.",
                    ephemeral=True,
                )
                return
            user.ticket -= amount
            new_total = user.ticket
            session.commit()

        await interaction.response.send_message(
            f"✅ Đã xóa **{amount}** vé của {player.mention}. "
            f"Tổng vé còn lại: **{new_total}**.",
            ephemeral=True,
        )

    # ── View player FFA stats ─────────────────────────────────────────────────

    @bot.tree.command(
        name="view_ffa",
        description="Xem thông tin FFA của một người chơi.",
        guild=guild_obj,
    )
    @app_commands.describe(player="Người chơi cần xem thông tin")
    async def view_ffa(
        interaction: discord.Interaction,
        player: discord.Member,
    ) -> None:
        """Display a rich embed with a player's FFA stats."""
        from entity import User

        with db_session_factory() as session:
            user = session.get(User, player.id)

        if user is None:
            await interaction.response.send_message(
                f"❌ {player.mention} chưa có hồ sơ FFA. "
                "Hãy yêu cầu họ dùng `/set_ingame_name` trước.",
                ephemeral=True,
            )
            return

        # Format last ELO change indicator
        change = user.last_elo_change or 0
        if change > 0:
            change_str = f"📈 +{change}"
        elif change < 0:
            change_str = f"📉 {change}"
        else:
            change_str = "—"

        monthly = user.monthly_elo_gain or 0
        monthly_str = f"🔥 +{monthly}" if monthly > 0 else "—"

        embed = discord.Embed(
            title=f"🎮 Hồ Sơ FFA — {player.display_name}",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=player.display_avatar.url)
        embed.add_field(name="🕹️ Tên in-game", value=user.ingame_name or "_chưa đặt_", inline=True)
        embed.add_field(name="⚔️ ELO", value=f"**{user.elo}**", inline=True)
        embed.add_field(name="🎫 Vé", value=str(user.ticket), inline=True)
        embed.add_field(name="📈 Biến động gần nhất", value=change_str, inline=True)
        embed.add_field(name="🔥 ELO tăng tháng này", value=monthly_str, inline=True)
        embed.add_field(
            name="📅 Tham gia từ",
            value=user.created_date.strftime("%d/%m/%Y") if user.created_date else "—",
            inline=True,
        )
        embed.set_footer(text=f"Discord ID: {player.id}")

        await interaction.response.send_message(embed=embed)
