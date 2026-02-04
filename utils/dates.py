"""Date helpers for consistent local timezone handling."""

from datetime import datetime, timedelta
from typing import Optional

from config import Config


def _local_offset() -> timedelta:
    return timedelta(hours=Config.TIMEZONE_OFFSET_HOURS, minutes=Config.TIMEZONE_OFFSET_MINUTES)


def local_datetime(dt: Optional[datetime]) -> Optional[datetime]:
    if not dt:
        return dt
    return dt + _local_offset()


def local_date(dt: Optional[datetime]):
    local_dt = local_datetime(dt)
    return local_dt.date() if local_dt else None
