from __future__ import annotations

from datetime import date, datetime, time, timezone

UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def start_of_day_utc(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=UTC)


def end_of_day_utc(value: date) -> datetime:
    return datetime.combine(value, time(23, 59, 59), tzinfo=UTC)


def parse_date(value: str) -> date:
    return date.fromisoformat(value)
