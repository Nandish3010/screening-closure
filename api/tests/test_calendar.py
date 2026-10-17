"""T-A12 and T-A13: the working-day calendar (UAE default, configurable)."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app import calendar as cal
from app.calendar import (
    CalendarError,
    WorkingCalendar,
    calendar_from_dict,
    load_calendar,
)
from app.config import settings as app_settings

DXB = ZoneInfo("Asia/Dubai")
UAE = WorkingCalendar()  # Mon-Fri working, Sat-Sun weekend, Asia/Dubai


def dxb(y, m, d, hh=10, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=DXB)


# 2026-01-05 is a Monday. Every expected value below was counted by hand on a wall calendar.
DUE_TABLE = [
    # (start, working days, expected due)                                   comment
    (dxb(2026, 1, 8), 2, dxb(2026, 1, 12)),             # Thu + 2 -> next Mon (T-A12 headline)
    (dxb(2026, 1, 10), 2, dxb(2026, 1, 14, 0)),         # Sat counts from Mon 00:00 -> Wed
    (dxb(2026, 1, 11, 12), 0, dxb(2026, 1, 12, 0)),     # Sun + 0 -> Mon 00:00
    (dxb(2026, 1, 5, 9), 1, dxb(2026, 1, 6, 9)),        # Mon + 1 -> Tue
    (dxb(2026, 1, 9), 1, dxb(2026, 1, 12)),             # Fri + 1 -> Mon (skips the weekend)
    (dxb(2026, 1, 9), 2, dxb(2026, 1, 13)),             # Fri + 2 -> Tue
    (dxb(2026, 1, 5), 5, dxb(2026, 1, 12)),             # Mon + 5 -> next Mon
    (dxb(2026, 1, 8), 14, dxb(2026, 1, 28)),            # Thu + 14 (book_confirmatory SLA)
    (dxb(2026, 1, 5), 45, dxb(2026, 3, 9)),             # Mon + 45 = nine weeks (complete SLA)
    (dxb(2026, 1, 10), 14, dxb(2026, 1, 30, 0)),        # Sat + 14 -> Mon 12th + 14 -> Fri 30th
    (dxb(2026, 1, 9, 23, 59), 1, dxb(2026, 1, 12, 23, 59)),  # Fri late + 1 keeps time of day
    (dxb(2026, 1, 6), 0, dxb(2026, 1, 6)),              # working-day start + 0 is itself
    (date(2026, 1, 8), 2, date(2026, 1, 12)),           # plain dates work too
    (date(2026, 1, 10), 1, date(2026, 1, 13)),          # Sat date + 1 -> Mon + 1 = Tue
]


@pytest.mark.parametrize("start,n,expected", DUE_TABLE)
def test_due_dates_match_hand_computed_table(start, n, expected):
    due = UAE.add_working_days(start, n)
    assert due == expected
    assert UAE.is_working_day(due), "a deadline never lands on a weekend"


def test_at_least_ten_table_cases():
    assert len(DUE_TABLE) >= 10


def test_weekday_assumption_in_the_table():
    assert date(2026, 1, 5).weekday() == 0  # Monday
    assert date(2026, 1, 8).weekday() == 3  # Thursday
    assert date(2026, 1, 10).weekday() == 5  # Saturday


def test_calendar_day_is_the_local_day_not_the_utc_day():
    # Thursday 22:00 UTC is already Friday 02:00 in Dubai: +1 working day is Monday, not Friday.
    start = datetime(2026, 1, 8, 22, 0, tzinfo=UTC)
    assert UAE.add_working_days(start, 1) == datetime(2026, 1, 11, 22, 0, tzinfo=UTC)
    assert UAE.add_working_days(start, 1).astimezone(DXB) == dxb(2026, 1, 12, 2)
    # Friday 22:00 UTC is Saturday 02:00 local: the weekend, so it counts from Monday 00:00.
    sat_local = datetime(2026, 1, 9, 22, 0, tzinfo=UTC)
    assert UAE.add_working_days(sat_local, 0).astimezone(DXB) == dxb(2026, 1, 12, 0)


def test_result_keeps_the_timezone_of_the_input():
    assert UAE.add_working_days(dxb(2026, 1, 8), 2).tzinfo is DXB
    utc_due = UAE.add_working_days(datetime(2026, 1, 8, 6, 0, tzinfo=UTC), 2)
    assert utc_due.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "when,expected",
    [
        (date(2026, 1, 5), True), (date(2026, 1, 9), True),   # Mon, Fri
        (date(2026, 1, 10), False), (date(2026, 1, 11), False),  # Sat, Sun
        (datetime(2026, 1, 9, 21, 0, tzinfo=UTC), False),       # Fri 21:00Z is Sat 01:00 local
        (datetime(2026, 1, 11, 21, 0, tzinfo=UTC), True),       # Sun 21:00Z is Mon 01:00 local
    ],
)
def test_is_working_day(when, expected):
    assert UAE.is_working_day(when) is expected


def test_working_days_between_hand_checked():
    assert UAE.working_days_between(dxb(2026, 1, 8), dxb(2026, 1, 12)) == 2
    assert UAE.working_days_between(dxb(2026, 1, 5), dxb(2026, 1, 12)) == 5
    assert UAE.working_days_between(dxb(2026, 1, 12), dxb(2026, 1, 12)) == 0
    # A Saturday start counts from Monday, so Sat -> Mon is zero working days elapsed.
    assert UAE.working_days_between(dxb(2026, 1, 10), dxb(2026, 1, 12)) == 0
    assert UAE.working_days_between(dxb(2026, 1, 10), dxb(2026, 1, 13)) == 1
    assert UAE.working_days_between(dxb(2026, 1, 12), dxb(2026, 1, 8)) == -2  # reversed


def test_holidays_are_skipped():
    with_holiday = WorkingCalendar(holidays=frozenset({date(2026, 1, 12)}))  # the Monday
    assert with_holiday.add_working_days(dxb(2026, 1, 8), 2) == dxb(2026, 1, 13)
    assert not with_holiday.is_working_day(date(2026, 1, 12))
    # A start on a holiday counts from the next working day.
    assert with_holiday.add_working_days(dxb(2026, 1, 12), 1) == dxb(2026, 1, 14, 0)


def test_alternative_weekend_changes_results():
    fri_sat = WorkingCalendar(weekend=frozenset({4, 5}))
    start = dxb(2026, 1, 7)  # Wednesday
    assert UAE.add_working_days(start, 2) == dxb(2026, 1, 9)    # Thu, Fri
    assert fri_sat.add_working_days(start, 2) == dxb(2026, 1, 11)  # Thu, then Sun
    assert fri_sat.is_working_day(date(2026, 1, 11)) and not fri_sat.is_working_day(date(2026, 1, 9))


def test_negative_days_and_naive_datetimes_are_rejected():
    with pytest.raises(CalendarError):
        UAE.add_working_days(dxb(2026, 1, 8), -1)
    with pytest.raises(CalendarError):
        UAE.add_working_days(datetime(2026, 1, 8, 10, 0), 1)


# ---- configuration --------------------------------------------------------------------


def test_repo_calendar_yaml_is_the_uae_default():
    loaded = load_calendar(app_settings.calendar_path)
    assert loaded.weekend == frozenset({5, 6})
    assert loaded.holidays == frozenset()  # empty on purpose, see ADR-009
    assert str(loaded.tz) == "Asia/Dubai"
    assert loaded.add_working_days(dxb(2026, 1, 8), 2) == dxb(2026, 1, 12)


def test_default_calendar_is_loaded_once():
    cal.default_calendar.cache_clear()
    first = cal.default_calendar()
    assert cal.default_calendar() is first
    assert cal.add_working_days(date(2026, 1, 8), 2) == date(2026, 1, 12)
    assert cal.is_working_day(date(2026, 1, 5))
    assert cal.working_days_between(date(2026, 1, 8), date(2026, 1, 12)) == 2


def test_config_file_with_friday_saturday_weekend_and_holidays(tmp_path):
    f = tmp_path / "calendar.yaml"
    f.write_text(
        "timezone: UTC\nweekend: [Friday, saturday]\n"
        "holidays:\n  - 2026-01-13\n  - {date: 2026-01-14, name: test}\n"
    )
    c = load_calendar(f)
    assert c.weekend == frozenset({4, 5})
    assert c.holidays == frozenset({date(2026, 1, 13), date(2026, 1, 14)})
    # Wed 7th + 4: Thu 8 (1), Sun 11 (2), Mon 12 (3), Tue 13 and Wed 14 are holidays, Thu 15 (4)
    assert c.add_working_days(datetime(2026, 1, 7, 9, tzinfo=UTC), 4) == datetime(
        2026, 1, 15, 9, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "raw",
    [
        {"weekend": ["funday"]},
        {"weekend": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]},
        {"holidays": ["not-a-date"]},
        {"timezone": "Mars/Olympus"},
    ],
)
def test_bad_config_is_rejected(raw):
    with pytest.raises(CalendarError):
        calendar_from_dict(raw)


def test_empty_config_means_uae_defaults():
    c = calendar_from_dict({})
    assert c.weekend == frozenset({5, 6}) and str(c.tz) == "Asia/Dubai"


# ---- properties (T-A13) ---------------------------------------------------------------

instants = st.datetimes(
    min_value=datetime(2024, 1, 1), max_value=datetime(2031, 12, 31), timezones=st.just(UTC)
)
weekends = st.sets(st.integers(0, 6), max_size=6).map(frozenset)
holiday_sets = st.sets(
    st.dates(min_value=date(2024, 1, 1), max_value=date(2032, 6, 30)), max_size=30
).map(frozenset)
calendars = st.builds(WorkingCalendar, weekend=weekends, holidays=holiday_sets)


@settings(max_examples=400, deadline=None)
@given(calendars, instants, st.integers(0, 60))
def test_property_due_is_working_day_and_counts_back_to_sla_length(c, start, n):
    due = c.add_working_days(start, n)
    assert c.is_working_day(due)
    assert c.working_days_between(start, due) == n
    assert due >= start


@settings(max_examples=400, deadline=None)
@given(calendars, instants, st.integers(0, 60))
def test_property_due_is_strictly_monotonic_in_sla_length(c, start, n):
    assert c.add_working_days(start, n + 1) > c.add_working_days(start, n)


@settings(max_examples=400, deadline=None)
@given(calendars, instants, st.integers(0, 3000), st.integers(0, 60))
def test_property_due_is_monotonic_in_start(c, start, minutes, n):
    later = start + timedelta(minutes=minutes)
    assert c.add_working_days(later, n) >= c.add_working_days(start, n)


@settings(max_examples=200, deadline=None)
@given(instants, st.integers(0, 60))
def test_property_default_calendar_never_lands_on_a_weekend(start, n):
    due = UAE.add_working_days(start, n).astimezone(DXB)
    assert due.weekday() < 5


@settings(max_examples=200, deadline=None)
@given(instants, st.integers(1, 60))
def test_property_alternative_weekend_lands_on_its_own_working_days(start, n):
    fri_sat = WorkingCalendar(weekend=frozenset({4, 5}))
    due = fri_sat.add_working_days(start, n).astimezone(DXB)
    assert due.weekday() not in (4, 5)
