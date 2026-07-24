from __future__ import annotations

import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def parse_retry_after_seconds(
    value: str | None,
    *,
    now: datetime | None = None,
) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = (retry_at - (now or datetime.now(UTC))).total_seconds()
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


__all__ = ["parse_retry_after_seconds"]
