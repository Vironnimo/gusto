"""UTC timestamps and ordering used by persisted records and sync."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO timestamp used by the shopping sync.

    Both the former second-precision format and the current millisecond format
    are accepted so existing data remains compatible.
    """
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _version_key(value: str) -> tuple:
    """Comparable sync version; valid ISO values beat malformed legacy data."""
    parsed = _parse_iso(value)
    return (1, parsed) if parsed is not None else (0, value)


def _next_sync_instant(previous: datetime) -> datetime:
    """Advance a parsed version or reject a timestamp outside writable range."""
    try:
        return previous + timedelta(milliseconds=1)
    except OverflowError as error:
        raise ValueError(
            "Der Zeitstempel eines Einkaufslisten-Eintrags liegt zu weit "
            "in der Zukunft und kann nicht fortgeschrieben werden."
        ) from error


def _now_iso(after: str | None = None) -> str:
    """Current UTC timestamp with milliseconds and a literal ``Z``.

    When ``after`` belongs to the same item, the result is guaranteed to be
    newer. This prevents a rapid add-then-toggle sequence from producing two
    indistinguishable versions while remaining compatible with old timestamps.
    """
    now = datetime.now(timezone.utc)
    # Compare at the same precision that is actually persisted. Comparing the
    # original microseconds could make a same-millisecond change look newer
    # here, only to serialize it back to the previous identical sync version.
    now = now.replace(microsecond=(now.microsecond // 1000) * 1000)
    previous = _parse_iso(after or "")
    if previous is not None and now <= previous:
        now = _next_sync_instant(previous)
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
