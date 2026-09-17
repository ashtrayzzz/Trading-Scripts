"""Time and calendar utilities with strict UTC enforcement."""

from datetime import datetime, time, timedelta, timezone
from typing import Union

from src.core.enums import TimeInterval


INTERVAL_DURATIONS: dict[str, timedelta] = {
    TimeInterval.M15: timedelta(minutes=15),
    TimeInterval.H1: timedelta(hours=1),
    TimeInterval.H4: timedelta(hours=4),
    TimeInterval.D1: timedelta(days=1),
    TimeInterval.W1: timedelta(weeks=1),
    TimeInterval.M1: timedelta(days=30),
}


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_iso_utc(dt: datetime) -> str:
    """Convert datetime to ISO 8601 UTC string."""
    return ensure_utc(dt).isoformat()


def from_timestamp_ms(ts_ms: Union[int, float]) -> datetime:
    """Convert millisecond timestamp to timezone-aware UTC datetime."""
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)


def to_timestamp_ms(dt: datetime) -> int:
    """Convert datetime to millisecond UTC timestamp."""
    return int(ensure_utc(dt).timestamp() * 1000)


def interval_to_timedelta(interval: Union[str, TimeInterval]) -> timedelta:
    """Return timedelta for a standard interval."""
    interval_str = str(interval)
    if interval_str in INTERVAL_DURATIONS:
        return INTERVAL_DURATIONS[interval_str]
    # Fallback parsing
    if interval_str.endswith("m"):
        return timedelta(minutes=int(interval_str[:-1]))
    if interval_str.endswith("h"):
        return timedelta(hours=int(interval_str[:-1]))
    if interval_str.endswith("d"):
        return timedelta(days=int(interval_str[:-1]))
    if interval_str.endswith("w"):
        return timedelta(weeks=int(interval_str[:-1]))
    if interval_str.endswith("M"):
        return timedelta(days=int(interval_str[:-1]) * 30)
    raise ValueError(f"Unsupported interval: {interval}")


def compute_bar_close_time(open_time: datetime, interval: Union[str, TimeInterval]) -> datetime:
    """Calculate the exact closing timestamp for a bar."""
    return ensure_utc(open_time) + interval_to_timedelta(interval)


def is_bar_closed(open_time: datetime, interval: Union[str, TimeInterval], as_of: datetime | None = None) -> bool:
    """
    Check if a bar is fully closed.
    
    CRITICAL ARCHITECTURAL INVARIANT:
    Closed-bar scanners MUST NEVER evaluate on unclosed bars.
    """
    now = ensure_utc(as_of or utc_now())
    close_time = compute_bar_close_time(open_time, interval)
    return close_time <= now


def get_breakoutprop_daily_window(as_of: datetime | None = None) -> tuple[datetime, datetime]:
    """
    Breakoutprop resets daily limits at 00:30 UTC.
    
    Returns:
        (window_start, window_end): The start time of the active 24h cycle
        and the upcoming reset time.
    """
    now = ensure_utc(as_of or utc_now())
    reset_time = time(hour=0, minute=30, tzinfo=timezone.utc)
    today_reset = datetime.combine(now.date(), reset_time)
    
    if now >= today_reset:
        window_start = today_reset
        window_end = today_reset + timedelta(days=1)
    else:
        window_start = today_reset - timedelta(days=1)
        window_end = today_reset
        
    return window_start, window_end


def format_dual_time(dt: datetime | None, local_tz_name: str | None = None) -> str:
    """
    Format a datetime in UTC with device/local time in brackets.
    Automatically detects user's device local timezone (e.g. PDT or EDT).
    
    Example: '2026-09-17 03:18 UTC (20:18 PDT)'
    """
    if dt is None:
        return "—"

    utc_dt = ensure_utc(dt)
    utc_str = utc_dt.strftime("%Y-%m-%d %H:%M UTC")

    if local_tz_name:
        try:
            from zoneinfo import ZoneInfo
            local_tz = ZoneInfo(local_tz_name)
        except Exception:
            local_tz = datetime.now().astimezone().tzinfo or timezone.utc
    else:
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc

    local_dt = utc_dt.astimezone(local_tz)
    local_tz_abbr = local_dt.strftime("%Z")
    local_str = local_dt.strftime(f"%H:%M {local_tz_abbr}")

    return f"{utc_str} ({local_str})"


def get_dual_clock_status() -> dict[str, str]:
    """
    Return current UTC time and device local time for ambient display headers.
    """
    now_utc = utc_now()
    now_local = datetime.now().astimezone()
    tz_abbr = now_local.strftime("%Z")

    return {
        "utc_str": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "local_str": now_local.strftime(f"%Y-%m-%d %H:%M:%S {tz_abbr}"),
        "tz_abbr": tz_abbr,
    }


