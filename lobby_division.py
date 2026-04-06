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

import logging
import random
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from discord.ext import commands as ext_commands

log = logging.getLogger(__name__)

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



# ── Embed builders ─────────────────────────────────────────────────────────────

def build_lobby_display_embed(
    lobby,
    match,
    p_map: dict[int, str],
) -> discord.Embed:
    """Build the public display embed that shows players and their civ assignments.

    Uses the embed description as a table so that all fights appear on one row
    per player regardless of how many fights there are.  This avoids the
    Discord 3-inline-fields-per-row limit that caused extra fights to wrap.

    Layout::

        Người chơi   | Trận 1   | Trận 2   | Trận 3   | Trận 4
        ─────────────────────────────────────────────────────────
        PlayerIngame | <civ>    | <civ>    | <civ>    | <civ>
        AI           | <civ>    | <civ>    | <civ>    | <civ>
    """
    tier = lobby.tier
    emoji = TIER_EMOJI.get(tier, "🎮")
    title = f"{emoji} Match #{match.id} | Lobby {tier} #{lobby.lobby_number}"

    count_fight: int = match.count_fight
    civs: dict = lobby.civs or {}
    users_list: list = lobby.users_list or []
    ai_count: int = lobby.ai_count or 0

    # Build ordered player entries: (display_name, civ_key)
    player_entries: list[tuple[str, str]] = [
        (p_map.get(uid, "Unknown"), str(uid)) for uid in users_list
    ]
    for idx in range(1, ai_count + 1):
        player_entries.append(("AI", f"AI_{idx}"))

    # Header row: fight labels
    fight_labels = " · ".join(f"**Trận {i}**" for i in range(1, count_fight + 1))
    header = f"**Người chơi** ─── {fight_labels}"
    separator = "─" * 40

    # One line per player showing all their civs
    rows: list[str] = []
    for name, key in player_entries:
        player_civs = civs.get(key, [])
        civ_strs = [
            player_civs[i - 1] if i - 1 < len(player_civs) else "—"
            for i in range(1, count_fight + 1)
        ]
        rows.append(f"**{name}**: " + " · ".join(civ_strs))

    description = header + "\n" + separator + "\n" + "\n".join(rows)

    embed = discord.Embed(title=title, description=description, color=discord.Color.gold())
    embed.set_footer(text=f"Match #{match.id} | ID lobby #{lobby.id}")
    return embed


# ── Discord channel creation ───────────────────────────────────────────────────

async def create_lobby_channels(
    guild: discord.Guild,
    lobby,
    match,
    p_map: dict[int, str],
    category: discord.CategoryChannel | None,
    judge_role: discord.Role | None,
) -> tuple[list[int], list[int]]:
    """Create one voice channel and one text channel per fight for the lobby.

    Channels are visible only to players in the lobby, admins, and holders of
    *judge_role* (if provided).

    Returns
    -------
    (voice_channel_ids, text_channel_ids)
        Lists of Discord channel IDs, indexed by fight number (0-based).
    """
    # Permission overwrites: hide from everyone by default
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }
    if judge_role:
        overwrites[judge_role] = discord.PermissionOverwrite(
            view_channel=True, connect=True
        )
    for uid in (lobby.users_list or []):
        member = guild.get_member(uid)
        if member:
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=True, connect=True, send_messages=True
            )

    tier_short = {
        TIER_LEGENDARY: "hl",
        TIER_CONQUEST: "cp",
        TIER_DIAMOND: "kc",
        TIER_RECRUIT: "tb",
    }.get(lobby.tier, "lby")
    map_names: list = match.name_maps or []

    voice_ids: list[int] = []
    text_ids: list[int] = []

    for i in range(1, match.count_fight + 1):
        map_name = map_names[i - 1] if i - 1 < len(map_names) else f"map{i}"
        # Sanitise map name for channel name (Discord channel names: lowercase, no spaces)
        map_slug = map_name.lower().replace(" ", "-")

        vc = await guild.create_voice_channel(
            name=f"🎮 Lobby {lobby.tier} #{lobby.lobby_number} · Trận {i}",
            overwrites=overwrites,
            category=category,
        )
        voice_ids.append(vc.id)

        tc = await guild.create_text_channel(
            name=f"lobby-{tier_short}{lobby.lobby_number}-tran{i}-{map_slug}",
            overwrites=overwrites,
            category=category,
        )
        text_ids.append(tc.id)

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
        RESULT_CHANNEL_ID,
        JUDGE_ROLE_ID,
        LOBBY_CATEGORY_ID,
    )
    from views import LobbyResultView, build_lobby_result_embed

    announce_channel = bot.get_channel(DIVIDE_LOBBY_CHANNEL_ID)
    result_channel = bot.get_channel(RESULT_CHANNEL_ID) if RESULT_CHANNEL_ID else None

    # Guard: skip if lobbies have already been created for this match
    # (prevents double-execution when the scheduler ticks twice within the same minute)
    with db_session_factory() as session:
        existing_count = session.query(Lobby).filter(Lobby.match_id == match.id).count()
    if existing_count > 0:
        return

    checkin_ids: list = match.checkin_users_id or []
    if not checkin_ids:
        if announce_channel:
            await announce_channel.send(
                f"⚠️ **Match #{match.id}** – Không có người chơi nào check-in, "
                "không thể chia lobby."
            )
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
        await announce_channel.send(
            f"⚠️ **Match #{match.id}** – Những người chơi sau không đủ điều kiện "
            f"tham gia (ELO cao hơn ELO thấp nhất của nhóm có vé nhưng không có vé): "
            f"{names}"
        )

    # ── Step 4: Build list of lobby specs ─────────────────────────────────────
    # Each spec: (tier, lobby_number, [user_ids], ai_count)
    LobbySpec = tuple  # (str, int, list[int], int)
    lobby_specs: list[LobbySpec] = []

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
                        announce_channel.send(
                            f"❌ **Match #{match.id}** – "
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
                        announce_channel.send(
                            f"❌ **Match #{match.id}** – "
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
            await announce_channel.send(
                f"❌ **Match #{match.id}** – Nhóm có vé bị hủy vì không đủ số người "
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
            await announce_channel.send(
                f"❌ **Match #{match.id}** – Nhóm không vé bị hủy vì không đủ số người "
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
                        announce_channel.send(
                            f"❌ **Match #{match.id}** – "
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
            await announce_channel.send(
                f"⚠️ **Match #{match.id}** – Không có lobby nào được tạo."
            )
        return

    # ── Step 5: Build player name map ─────────────────────────────────────────
    all_player_ids = list({uid for _, _, players, _ in lobby_specs for uid in players})
    with db_session_factory() as session:
        users = session.query(User).filter(User.id.in_(all_player_ids)).all()
    p_map = {u.id: (u.ingame_name or "Unknown") for u in users}

    # ── Step 6: Guild / role / category objects ────────────────────────────────
    guild = bot.guilds[0] if bot.guilds else None
    judge_role = guild.get_role(JUDGE_ROLE_ID) if (guild and JUDGE_ROLE_ID) else None
    category = (
        guild.get_channel(LOBBY_CATEGORY_ID)
        if (guild and LOBBY_CATEGORY_ID)
        else None
    )

    # ── Step 7: Persist lobbies, create channels, post embeds ─────────────────
    if announce_channel:
        await announce_channel.send(
            f"✅ **Match #{match.id}** – Bắt đầu chia {len(lobby_specs)} lobby…"
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
                await announce_channel.send(
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
            display_embed = build_lobby_display_embed(lobby_snap, match_snap, p_map)
            mentions = " ".join(f"<@{uid}>" for uid in (lobby_snap.users_list or []))
            await announce_channel.send(content=mentions or None, embed=display_embed)

        # Send result-entry embed + buttons to result channel
        if result_channel:
            result_embed = build_lobby_result_embed(lobby_snap, match_snap, p_map)
            view = LobbyResultView(
                lobby_id=lobby_id,
                count_fight=match.count_fight,
                map_names=match.name_maps or [],
                db_session_factory=db_session_factory,
            )
            result_msg = await result_channel.send(embed=result_embed, view=view)

            with db_session_factory() as session:
                db_lobby = session.get(Lobby, lobby_id)
                if db_lobby:
                    db_lobby.result_message_id = result_msg.id
                    session.commit()


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
