"""Timestamp helpers. Stored timestamps are always UTC ISO-8601 with a trailing 'Z'."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def format_local(value: str | None) -> str:
    """'2026-11-24T17:00:00Z' -> '2026-11-24 20:00 (UTC+03:00)' in the machine's local zone."""
    if not value:
        return "unknown"
    try:
        dt = parse_iso(value).astimezone()
    except ValueError:
        return f"{value} (unparsed)"
    off = dt.strftime("%z") or "+0000"
    return f"{dt:%Y-%m-%d %H:%M} (UTC{off[:3]}:{off[3:]})"
