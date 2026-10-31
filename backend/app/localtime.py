"""
Clinic-local time. The DB stores UTC; everything a human reads (tracker, SMS, dashboard)
or types (slot "09:20", booking time) is clinic-local.

Rule: convert at the edges — parse local input to UTC before it touches the DB, format
UTC to local only when rendering text.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import settings

# ponytail: one zone for every clinic; add clinics.timezone when we go beyond Algeria
CLINIC_TZ = ZoneInfo(settings.CLINIC_TIMEZONE)


def aware(dt: datetime) -> datetime:
    # Postgres returns aware datetimes; SQLite (tests) returns naive UTC
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_local(dt: datetime) -> datetime:
    return aware(dt).astimezone(CLINIC_TZ)


def hhmm(dt: datetime) -> str:
    return to_local(dt).strftime("%H:%M")


def local_at(d: date, t: time) -> datetime:
    """Wall-clock time at the clinic on day `d`, as UTC (ready for the DB)."""
    return datetime.combine(d, t, CLINIC_TZ).astimezone(timezone.utc)


def parse_local(value: datetime) -> datetime:
    """Client-supplied datetime -> UTC. Naive = clinic wall clock; explicit offsets are honoured."""
    return (value.replace(tzinfo=CLINIC_TZ) if value.tzinfo is None else value).astimezone(timezone.utc)


def day_bounds(d: date) -> tuple[datetime, datetime]:
    """Clinic-local calendar day `d` as a [start, end) UTC window."""
    return local_at(d, time(0)), local_at(d + timedelta(days=1), time(0))


def today() -> date:
    return datetime.now(CLINIC_TZ).date()
