import re
from datetime import datetime, timedelta, timezone

# Vietnam Standard Time - UTC+7 (no DST)
VN_TZ = timezone(timedelta(hours=7))


def parse_duration(value: str) -> timedelta:
    """Convert a human-readable duration string to a :class:`timedelta`.

    Supported formats:
    - ``"1h"``  → 1 hour
    - ``"30p"`` → 30 minutes
    """
    m = re.fullmatch(r"(\d+)([hp])", value.strip().lower())
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
