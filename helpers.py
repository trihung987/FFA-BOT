import re
from datetime import datetime, timedelta, timezone

# Vietnam Standard Time - UTC+7 (no DST)
VN_TZ = timezone(timedelta(hours=7))


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
