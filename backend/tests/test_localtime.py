from datetime import date, datetime, timedelta, timezone

from app.localtime import day_bounds, hhmm, parse_local
from app.routers.appointments import _parse_slot

UTC_9 = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)


def test_user_facing_times_are_algiers_not_utc():
    assert hhmm(UTC_9) == "10:00"
    assert hhmm(UTC_9.replace(tzinfo=None)) == "10:00"      # naive from SQLite = UTC


def test_local_input_is_stored_as_utc():
    assert parse_local(datetime(2026, 10, 31, 10, 0)) == UTC_9                     # naive = clinic wall clock
    assert parse_local(datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)) == UTC_9  # explicit offset kept
    # receptionist types "09:20" in the walk-in modal -> 08:20Z
    assert _parse_slot("09:20").astimezone(timezone.utc).strftime("%H:%M") == "08:20"


def test_clinic_day_starts_at_local_midnight():
    start, end = day_bounds(date(2026, 10, 31))
    assert start == datetime(2026, 10, 30, 23, 0, tzinfo=timezone.utc)
    assert end - start == timedelta(days=1)
